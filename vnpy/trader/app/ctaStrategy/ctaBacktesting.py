# encoding: UTF-8

'''
本文件中包含的是CTA模块的回测引擎，回测引擎的API和CTA引擎一致，
可以使用和实盘相同的代码进行回测。
'''
from __future__ import division

from datetime import datetime, timedelta
from collections import OrderedDict
from itertools import product
import multiprocessing
import copy
import math

import pymongo
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# 如果安装了seaborn则设置为白色风格
try:
    import seaborn as sns       
    sns.set_style('whitegrid')  
except ImportError:
    pass

from vnpy.trader.vtGlobal import globalSetting
from vnpy.trader.vtObject import VtTickData, VtBarData
from vnpy.trader.vtConstant import *
from vnpy.trader.vtGateway import VtOrderData, VtTradeData

from .ctaBase import *


########################################################################
class BacktestingEngine(object):
    """
    CTA回测引擎
    函数接口和策略引擎保持一样，
    从而实现同一套代码从回测到实盘。
    """
    
    TICK_MODE = 'tick'
    BAR_MODE = 'bar'

    #----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        # 本地停止单
        self.stopOrderCount = 0     # 编号计数：stopOrderID = STOPORDERPREFIX + str(stopOrderCount)
        
        # 本地停止单字典, key为stopOrderID，value为stopOrder对象
        self.stopOrderDict = {}             # 停止单撤销后不会从本字典中删除
        self.workingStopOrderDict = {}      # 停止单撤销后会从本字典中删除
        
        self.engineType = ENGINETYPE_BACKTESTING    # 引擎类型为回测
        
        self.strategy = None        # 回测策略
        self.mode = self.BAR_MODE   # 回测模式，默认为K线
        
        self.startDate = ''
        self.initDays = 0        
        self.endDate = ''

        self.capital = 1000000      # 回测时的起始本金（默认100万）
        self.slippage = 0           # 回测时假设的滑点
        self.slippageFunc = None
        self.rate = 0               # 回测时假设的佣金比例（适用于百分比佣金）
        self.rateFunc = None
        self.size = 1               # 合约大小，默认为1    
        self.priceTick = 0          # 价格最小变动 
        
        self.dbClient = None        # 数据库客户端
        self.dbCursor = None        # 数据库指针
        
        self.initData = []          # 初始化用的数据
        self.dbName = ''            # 回测数据库名
        self.symbol = ''            # 回测集合名
        
        self.dataStartDate = None       # 回测数据开始日期，datetime对象
        self.dataEndDate = None         # 回测数据结束日期，datetime对象
        self.strategyStartDate = None   # 策略启动日期（即前面的数据用于初始化），datetime对象
        
        self.limitOrderCount = 0                    # 限价单编号
        self.limitOrderDict = OrderedDict()         # 限价单字典
        self.workingLimitOrderDict = OrderedDict()  # 活动限价单字典，用于进行撮合用
        
        self.tradeCount = 0             # 成交编号
        self.tradeDict = OrderedDict()  # 成交字典
        
        self.logList = []               # 日志记录
        
        # 当前最新数据，用于模拟成交用
        self.tick = None
        self.bar = None
        self.dt = None      # 最新的时间
        
        # 日线回测结果计算用
        # self.dailyResultDict = OrderedDict()
        self.dailyResultDict = {}

        # Keep using the same plt fig to save memory and fd
        self.plt_fig_num_map = {}
    
    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        self.close()

    #------------------------------------------------
    # 通用功能
    #------------------------------------------------    
    #----------------------------------------------------------------------
    def close(self):
        for number in self.plt_fig_num_map.values():
            plt.close(number)

    #----------------------------------------------------------------------
    def get_new_figure(self, figsize=(10, 16)):
        fig = None
        plt_fig_num = self.plt_fig_num_map.get(figsize, -1)

        if plt_fig_num == -1:
            fig = plt.figure(figsize=figsize)
            self.plt_fig_num_map[figsize] = fig.number
        else:
            fig = plt.figure(num=plt_fig_num, clear=True)

        return fig

    #----------------------------------------------------------------------
    def roundToPriceTick(self, price):
        """取整价格到合约最小价格变动"""
        if not self.priceTick:
            return price
        
        newPrice = round(price/self.priceTick, 0) * self.priceTick
        return newPrice

    #----------------------------------------------------------------------
    def output(self, content):
        """输出内容"""
        print str(datetime.now()) + "\t" + content     
    
    #------------------------------------------------
    # 参数设置相关
    #------------------------------------------------
    
    #----------------------------------------------------------------------
    def setStartDate(self, startDate='20100416', initDays=10):
        """设置回测的启动日期"""
        self.startDate = startDate
        self.initDays = initDays
        
        initTimeDelta = timedelta(initDays)
        self.strategyStartDate = datetime.strptime(startDate, '%Y%m%d')
        self.dataStartDate = datetime.strptime(startDate, '%Y%m%d') - initTimeDelta
        
    #----------------------------------------------------------------------
    def setEndDate(self, endDate=''):
        """设置回测的结束日期"""
        self.endDate = endDate
        
        if endDate:
            self.dataEndDate = datetime.strptime(endDate, '%Y%m%d')
            
            # 若不修改时间则会导致不包含dataEndDate当天数据
            self.dataEndDate = self.dataEndDate.replace(hour=23, minute=59)    
        
    #----------------------------------------------------------------------
    def setBacktestingMode(self, mode):
        """设置回测模式"""
        self.mode = mode
    
    #----------------------------------------------------------------------
    def setDatabase(self, dbName, symbol):
        """设置历史数据所用的数据库"""
        self.dbName = dbName
        self.symbol = symbol
    
    #----------------------------------------------------------------------
    def setCapital(self, capital):
        """设置资本金"""
        self.capital = capital
    
    #----------------------------------------------------------------------
    def setSlippage(self, slippage, slippageFunc=None):
        """设置滑点点数"""
        self.slippage = slippage
        if slippageFunc is not None:
            self.slippageFunc = slippageFunc
        
    #----------------------------------------------------------------------
    def setSize(self, size):
        """设置合约大小"""
        self.size = size
        
    #----------------------------------------------------------------------
    def setRate(self, rate, rateFunc=None):
        """设置佣金比例"""
        self.rate = rate
        if rateFunc is not None:
            self.rateFunc = rateFunc
        
    #----------------------------------------------------------------------
    def setPriceTick(self, priceTick):
        """设置价格最小变动"""
        self.priceTick = priceTick
    
    #------------------------------------------------
    # 数据回放相关
    #------------------------------------------------    
    
    #----------------------------------------------------------------------
    def loadHistoryData(self):
        """载入历史数据"""
        self.dbClient = pymongo.MongoClient(globalSetting['mongoHost'], globalSetting['mongoPort'])
        collections = {}
        for symbol in self.symbol.split(","): 
            collections[symbol] = self.dbClient[self.dbName][symbol]

        self.output(u'开始载入数据')
      
        # 首先根据回测模式，确认要使用的数据类
        if self.mode == self.BAR_MODE:
            dataClass = VtBarData
            func = self.newBar
        else:
            dataClass = VtTickData
            func = self.newTick

        # 载入初始化需要用的数据
        flt = {'datetime':{'$gte':self.dataStartDate,
                           '$lt':self.strategyStartDate}}        
        
        initCursors = {}
        for symbol, collection in collections.iteritems():
            initCursors[symbol] = collection.find(flt, max_time_ms=120000).sort('datetime')
        
        # 将数据从查询指针中读取出，并生成列表
        self.initData = []              # 清空initData列表
        symbols = self.symbol.split(",")
        tmpDataDict = {}
        while True:
            tmpDataDateTimeList = []
            stopCount = 0
            stopLevel = len(symbols)
            for symbol in symbols:
                # check whether alive
                if not initCursors[symbol].alive:
                    stopCount = stopCount + 1
                    if stopCount == stopLevel:
                        break
                    continue
                if tmpDataDict.get(symbol) is not None:
                    oldData = tmpDataDict.get(symbol)
                    tmpDataDateTimeList.append(oldData.datetime)
                    continue
                try:
                    d = initCursors[symbol].next()
                except StopIteration:
                    continue
                data = dataClass()
                data.__dict__ = d
                tmpDataDict[symbol] = data
                tmpDataDateTimeList.append(data.datetime)

            # No data, break
            if not tmpDataDict:
                break

            tmpDataDateTimeList.sort()
            minDateTime = tmpDataDateTimeList[0]
            

            if len(set(tmpDataDateTimeList)) == 1:
                self.initData.append(tmpDataDict.copy())      
                tmpDataDict = {}
            else:
                dataDict = {}
                for symbol, data in tmpDataDict.items():
                    if data.datetime == minDateTime:
                        dataDict[symbol] = data
                        del tmpDataDict[symbol]
                    elif data.datetime < minDateTime:
                        del tmpDataDict[symbol]
                self.initData.append(dataDict)      

                # for d in initCursors[symbol]:
                #     data = [dataClass()]
                #     data.__dict__ = d
                #     self.initData.append(data)      
        
        # 载入回测数据
        if not self.dataEndDate:
            flt = {'datetime':{'$gte':self.strategyStartDate}}   # 数据过滤条件
        else:
            flt = {'datetime':{'$gte':self.strategyStartDate,
                               '$lte':self.dataEndDate}}  

        self.dbCursor = {}
        for symbol, collection in collections.iteritems():
            self.dbCursor[symbol] = collection.find(flt, max_time_ms=120000).sort('datetime')

        # self.dbCursor = collection.find(flt).sort('datetime')
        
        self.output(u'载入完成，数据量init：%s, after: %s' %(sum([initCursor.count() 
                                              for _, initCursor 
                                              in initCursors.items()]),
                                              sum([dbCursor.count() 
                                               for _, dbCursor
                                               in self.dbCursor.items()])))
        
    #----------------------------------------------------------------------
    def runBacktesting(self):
        """运行回测"""

        self.strategy.vtSymbol = self.symbol

        # 载入历史数据
        self.loadHistoryData()
        
        # 首先根据回测模式，确认要使用的数据类
        if self.mode == self.BAR_MODE:
            dataClass = VtBarData
            func = self.newBar
        else:
            dataClass = VtTickData
            func = self.newTick

        self.output(u'开始回测')
        
        self.strategy.inited = True
        self.strategy.onInit()
        self.output(u'策略初始化完成')
        
        self.strategy.trading = True
        self.strategy.onStart()
        self.output(u'策略启动完成')
        
        self.output(u'开始回放数据')

        symbols = self.symbol.split(",")
        tmpDataDict = {}
        while True:
            tmpDataDateTimeList = []
            stopCount = 0
            stopLevel = len(symbols)
            for symbol in symbols:
                # check whether alive
                if not self.dbCursor[symbol].alive:
                    stopCount = stopCount + 1
                    if stopCount == stopLevel:
                        break
                    continue

                if tmpDataDict.get(symbol) is not None:
                    oldData = tmpDataDict.get(symbol)
                    tmpDataDateTimeList.append(oldData.datetime)
                    continue
                try:
                    d = self.dbCursor[symbol].next()
                except StopIteration:
                    continue
                data = dataClass()
                data.__dict__ = d
                tmpDataDict[symbol] = data
                tmpDataDateTimeList.append(data.datetime)
            # No data, break
            if not tmpDataDict:
                break

            tmpDataDateTimeList.sort()
            minDateTime = tmpDataDateTimeList[0]

            if len(set(tmpDataDateTimeList)) == 1:
                func(tmpDataDict)     
                tmpDataDict = {}
                # self.initData.append(tmpDataDateTimeDict)      
            else:
                dataDict = {}
                for symbol, data in tmpDataDict.items():
                    if data.datetime == minDateTime:
                        dataDict[symbol] = data
                        del tmpDataDict[symbol]
                    elif data.datetime < minDateTime:
                        del tmpDataDict[symbol]

                func(dataDict)     
                # self.initData.append(tmpDataDateTimeDict)

        # for d in self.dbCursor:
        #     data = dataClass()
        #     data.__dict__ = d
        #     func(data)     
            
        self.output(u'数据回放结束')
        self.strategy.onStop()

    #----------------------------------------------------------------------
    def newBar(self, bar):
        """新的K线"""
        self.bar = bar
        symbols = bar.keys()
        symbol = symbols[0]
        self.dt = bar[symbol].datetime
        
        self.crossLimitOrder()      # 先撮合限价单
        self.crossStopOrder()       # 再撮合停止单
        self.strategy.onBar(bar)    # 推送K线到策略中
        
        for symbol, b in bar.items(): 
            self.updateDailyClose(symbol, b.datetime, b.close)
    
    #----------------------------------------------------------------------
    def newTick(self, tick):
        """新的Tick"""
        self.tick = tick
        self.dt = tick.datetime
        
        self.crossLimitOrder()
        self.crossStopOrder()
        self.strategy.onTick(tick)
        for symbol, t in tick.items(): 
            self.updateDailyClose(symbol, t.datetime, t.lastPrice)
        
    #----------------------------------------------------------------------
    def initStrategy(self, strategyClass, setting=None):
        """
        初始化策略
        setting是策略的参数设置，如果使用类中写好的默认设置则可以不传该参数
        """
        self.strategy = strategyClass(self, setting)
        self.strategy.pos = dict()
        self.strategy.name = self.strategy.className
    
    #----------------------------------------------------------------------
    def crossLimitOrder(self):
        """基于最新数据撮合限价单"""

        
        # 遍历限价单字典中的所有限价单
        for orderID, order in self.workingLimitOrderDict.items():
            # 先确定会撮合成交的价格
            symbol = order.vtSymbol
            if self.mode == self.BAR_MODE:
                buyCrossPrice = self.bar[symbol].low        # 若买入方向限价单价格高于该价格，则会成交
                sellCrossPrice = self.bar[symbol].high      # 若卖出方向限价单价格低于该价格，则会成交
                buyBestCrossPrice = self.bar[symbol].open   # 在当前时间点前发出的买入委托可能的最优成交价
                sellBestCrossPrice = self.bar[symbol].open  # 在当前时间点前发出的卖出委托可能的最优成交价
            else:
                buyCrossPrice = self.tick[symbol].askPrice1
                sellCrossPrice = self.tick[symbol].bidPrice1
                buyBestCrossPrice = self.tick[symbol].askPrice1
                sellBestCrossPrice = self.tick[symbol].bidPrice1
            # 推送委托进入队列（未成交）的状态更新
            if not order.status:
                order.status = STATUS_NOTTRADED
                self.strategy.onOrder(order)

            # 判断是否会成交
            buyCross = (order.direction==DIRECTION_LONG and 
                        order.price>=buyCrossPrice and
                        buyCrossPrice > 0)      # 国内的tick行情在涨停时askPrice1为0，此时买无法成交
            
            sellCross = (order.direction==DIRECTION_SHORT and 
                         order.price<=sellCrossPrice and
                         sellCrossPrice > 0)    # 国内的tick行情在跌停时bidPrice1为0，此时卖无法成交
            
            # 如果发生了成交
            if buyCross or sellCross:
                # 推送成交数据
                self.tradeCount += 1            # 成交编号自增1
                tradeID = str(self.tradeCount)
                trade = VtTradeData()
                trade.vtSymbol = order.vtSymbol
                trade.tradeID = tradeID
                trade.vtTradeID = tradeID
                trade.orderID = order.orderID
                trade.vtOrderID = order.orderID
                trade.direction = order.direction
                trade.offset = order.offset
                
                # 以买入为例：
                # 1. 假设当根K线的OHLC分别为：100, 125, 90, 110
                # 2. 假设在上一根K线结束(也是当前K线开始)的时刻，策略发出的委托为限价105
                # 3. 则在实际中的成交价会是100而不是105，因为委托发出时市场的最优价格是100
                if buyCross:
                    trade.price = min(order.price, buyBestCrossPrice)
                    self.strategy.pos[trade.vtSymbol] += order.totalVolume
                else:
                    trade.price = max(order.price, sellBestCrossPrice)
                    self.strategy.pos[trade.vtSymbol] -= order.totalVolume
                
                trade.volume = order.totalVolume
                trade.tradeTime = self.dt.strftime('%H:%M:%S')
                trade.dt = self.dt
                self.strategy.onTrade(trade)
                
                self.tradeDict[tradeID] = trade
                
                # 推送委托数据
                order.tradedVolume = order.totalVolume
                order.status = STATUS_ALLTRADED
                self.strategy.onOrder(order)
                
                # 从字典中删除该限价单
                if orderID in self.workingLimitOrderDict:
                    del self.workingLimitOrderDict[orderID]
                
    #----------------------------------------------------------------------
    def crossStopOrder(self):
        """基于最新数据撮合停止单"""
        # 遍历停止单字典中的所有停止单
        for stopOrderID, so in self.workingStopOrderDict.items():
            # 先确定会撮合成交的价格，这里和限价单规则相反
            symbol = so.vtSymbol
            if self.mode == self.BAR_MODE:
                buyCrossPrice = self.bar[symbol].high    # 若买入方向停止单价格低于该价格，则会成交
                sellCrossPrice = self.bar[symbol].low    # 若卖出方向限价单价格高于该价格，则会成交
                bestCrossPrice = self.bar[symbol].open   # 最优成交价，买入停止单不能低于，卖出停止单不能高于
            else:
                buyCrossPrice = self.tick[symbol].lastPrice
                sellCrossPrice = self.tick[symbol].lastPrice
                bestCrossPrice = self.tick[symbol].lastPrice
        

            # 判断是否会成交
            buyCross = so.direction==DIRECTION_LONG and so.price<=buyCrossPrice
            sellCross = so.direction==DIRECTION_SHORT and so.price>=sellCrossPrice
            
            # 如果发生了成交
            if buyCross or sellCross:
                # 更新停止单状态，并从字典中删除该停止单
                so.status = STOPORDER_TRIGGERED
                if stopOrderID in self.workingStopOrderDict:
                    del self.workingStopOrderDict[stopOrderID]                        

                # 推送成交数据
                self.tradeCount += 1            # 成交编号自增1
                tradeID = str(self.tradeCount)
                trade = VtTradeData()
                trade.vtSymbol = so.vtSymbol
                trade.tradeID = tradeID
                trade.vtTradeID = tradeID
                
                if buyCross:
                    self.strategy.pos[trade.vtSymbol] += so.volume
                    trade.price = max(bestCrossPrice, so.price)
                else:
                    self.strategy.pos[trade.vtSymbol] -= so.volume
                    trade.price = min(bestCrossPrice, so.price)                
                
                self.limitOrderCount += 1
                orderID = str(self.limitOrderCount)
                trade.orderID = orderID
                trade.vtOrderID = orderID
                trade.direction = so.direction
                trade.offset = so.offset
                trade.volume = so.volume
                trade.tradeTime = self.dt.strftime('%H:%M:%S')
                trade.dt = self.dt
                
                self.tradeDict[tradeID] = trade
                
                # 推送委托数据
                order = VtOrderData()
                order.vtSymbol = so.vtSymbol
                order.symbol = so.vtSymbol
                order.orderID = orderID
                order.vtOrderID = orderID
                order.direction = so.direction
                order.offset = so.offset
                order.price = so.price
                order.totalVolume = so.volume
                order.tradedVolume = so.volume
                order.status = STATUS_ALLTRADED
                order.orderTime = trade.tradeTime
                
                self.limitOrderDict[orderID] = order
                
                # 按照顺序推送数据
                self.strategy.onStopOrder(so)
                self.strategy.onOrder(order)
                self.strategy.onTrade(trade)
    
    #------------------------------------------------
    # 策略接口相关
    #------------------------------------------------      

    #----------------------------------------------------------------------
    def sendOrder(self, vtSymbol, orderType, price, volume, strategy):
        """发单"""
        self.limitOrderCount += 1
        orderID = str(self.limitOrderCount)
        
        order = VtOrderData()
        order.vtSymbol = vtSymbol
        order.price = self.roundToPriceTick(price)
        order.totalVolume = volume
        order.orderID = orderID
        order.vtOrderID = orderID
        order.orderTime = self.dt.strftime('%H:%M:%S')
        
        # CTA委托类型映射
        if orderType == CTAORDER_BUY:
            order.direction = DIRECTION_LONG
            order.offset = OFFSET_OPEN
        elif orderType == CTAORDER_SELL:
            order.direction = DIRECTION_SHORT
            order.offset = OFFSET_CLOSE
        elif orderType == CTAORDER_SHORT:
            order.direction = DIRECTION_SHORT
            order.offset = OFFSET_OPEN
        elif orderType == CTAORDER_COVER:
            order.direction = DIRECTION_LONG
            order.offset = OFFSET_CLOSE     
        
        # 保存到限价单字典中
        self.workingLimitOrderDict[orderID] = order
        self.limitOrderDict[orderID] = order
        
        return [orderID]
    
    #----------------------------------------------------------------------
    def cancelOrder(self, vtOrderID):
        """撤单"""
        if vtOrderID in self.workingLimitOrderDict:
            order = self.workingLimitOrderDict[vtOrderID]
            
            order.status = STATUS_CANCELLED
            order.cancelTime = self.dt.strftime('%H:%M:%S')
            
            self.strategy.onOrder(order)
            
            del self.workingLimitOrderDict[vtOrderID]
        
    #----------------------------------------------------------------------
    def sendStopOrder(self, vtSymbol, orderType, price, volume, strategy):
        """发停止单（本地实现）"""
        self.stopOrderCount += 1
        stopOrderID = STOPORDERPREFIX + str(self.stopOrderCount)
        
        so = StopOrder()
        so.vtSymbol = vtSymbol
        so.price = self.roundToPriceTick(price)
        so.volume = volume
        so.strategy = strategy
        so.status = STOPORDER_WAITING
        so.stopOrderID = stopOrderID
        
        if orderType == CTAORDER_BUY:
            so.direction = DIRECTION_LONG
            so.offset = OFFSET_OPEN
        elif orderType == CTAORDER_SELL:
            so.direction = DIRECTION_SHORT
            so.offset = OFFSET_CLOSE
        elif orderType == CTAORDER_SHORT:
            so.direction = DIRECTION_SHORT
            so.offset = OFFSET_OPEN
        elif orderType == CTAORDER_COVER:
            so.direction = DIRECTION_LONG
            so.offset = OFFSET_CLOSE           
        
        # 保存stopOrder对象到字典中
        self.stopOrderDict[stopOrderID] = so
        self.workingStopOrderDict[stopOrderID] = so
        
        # 推送停止单初始更新
        self.strategy.onStopOrder(so)        
        
        return [stopOrderID]
    
    #----------------------------------------------------------------------
    def cancelStopOrder(self, stopOrderID):
        """撤销停止单"""
        # 检查停止单是否存在
        if stopOrderID in self.workingStopOrderDict:
            so = self.workingStopOrderDict[stopOrderID]
            so.status = STOPORDER_CANCELLED
            del self.workingStopOrderDict[stopOrderID]
            self.strategy.onStopOrder(so)
    
    #----------------------------------------------------------------------
    def putStrategyEvent(self, name):
        """发送策略更新事件，回测中忽略"""
        pass
     
    #----------------------------------------------------------------------
    def insertData(self, dbName, collectionName, data):
        """考虑到回测中不允许向数据库插入数据，防止实盘交易中的一些代码出错"""
        pass
    
    #----------------------------------------------------------------------
    def loadBar(self, dbName, collectionName, startDate):
        """直接返回初始化数据列表中的Bar"""
        return self.initData
    
    #----------------------------------------------------------------------
    def loadTick(self, dbName, collectionName, startDate):
        """直接返回初始化数据列表中的Tick"""
        return self.initData
    
    #----------------------------------------------------------------------
    def writeCtaLog(self, content):
        """记录日志"""
        log = str(self.dt) + ' ' + content 
        self.logList.append(log)
    
    #----------------------------------------------------------------------
    def cancelAll(self, name):
        """全部撤单"""
        # 撤销限价单
        for orderID in self.workingLimitOrderDict.keys():
            self.cancelOrder(orderID)
        
        # 撤销停止单
        for stopOrderID in self.workingStopOrderDict.keys():
            self.cancelStopOrder(stopOrderID)

    #----------------------------------------------------------------------
    def saveSyncData(self, strategy):
        """保存同步数据（无效）"""
        pass
        

    #------------------------------------------------
    # 结果计算相关
    #------------------------------------------------      
    
    #----------------------------------------------------------------------
    def calculateBacktestingResult(self):
        """
        计算回测结果
        """
        self.output(u'计算回测结果')
        
        # 首先基于回测后的成交记录，计算每笔交易的盈亏
        resultList = []             # 交易结果列表
        
        longTrade = []              # 未平仓的多头交易
        shortTrade = []             # 未平仓的空头交易
        
        tradeTimeList = []          # 每笔成交时间戳
        posList = [0]               # 每笔成交后的持仓情况        

        for trade in self.tradeDict.values():
            # 复制成交对象，因为下面的开平仓交易配对涉及到对成交数量的修改
            # 若不进行复制直接操作，则计算完后所有成交的数量会变成0
            trade = copy.copy(trade)
            
            # 多头交易
            if trade.direction == DIRECTION_LONG:
                # 如果尚无空头交易
                if not shortTrade:
                    longTrade.append(trade)
                # 当前多头交易为平空
                else:
                    while True:
                        entryTrade = shortTrade[0]
                        exitTrade = trade
                        
                        # 清算开平仓交易
                        closedVolume = min(exitTrade.volume, entryTrade.volume)
                        result = TradingResult(entryTrade.price, entryTrade.dt, 
                                               exitTrade.price, exitTrade.dt,
                                               -closedVolume, self.rate, self.slippage, self.size,
                                               self.slippageFunc, self.rateFunc)
                        resultList.append(result)
                        
                        posList.extend([-1,0])
                        tradeTimeList.extend([result.entryDt, result.exitDt])
                        
                        # 计算未清算部分
                        entryTrade.volume -= closedVolume
                        exitTrade.volume -= closedVolume
                        
                        # 如果开仓交易已经全部清算，则从列表中移除
                        if not entryTrade.volume:
                            shortTrade.pop(0)
                        
                        # 如果平仓交易已经全部清算，则退出循环
                        if not exitTrade.volume:
                            break
                        
                        # 如果平仓交易未全部清算，
                        if exitTrade.volume:
                            # 且开仓交易已经全部清算完，则平仓交易剩余的部分
                            # 等于新的反向开仓交易，添加到队列中
                            if not shortTrade:
                                longTrade.append(exitTrade)
                                break
                            # 如果开仓交易还有剩余，则进入下一轮循环
                            else:
                                pass
                        
            # 空头交易        
            else:
                # 如果尚无多头交易
                if not longTrade:
                    shortTrade.append(trade)
                # 当前空头交易为平多
                else:                    
                    while True:
                        entryTrade = longTrade[0]
                        exitTrade = trade
                        
                        # 清算开平仓交易
                        closedVolume = min(exitTrade.volume, entryTrade.volume)
                        result = TradingResult(entryTrade.price, entryTrade.dt, 
                                               exitTrade.price, exitTrade.dt,
                                               closedVolume, self.rate, self.slippage, self.size,
                                               self.slippageFunc, self.rateFunc)
                        resultList.append(result)
                        
                        posList.extend([1,0])
                        tradeTimeList.extend([result.entryDt, result.exitDt])

                        # 计算未清算部分
                        entryTrade.volume -= closedVolume
                        exitTrade.volume -= closedVolume
                        
                        # 如果开仓交易已经全部清算，则从列表中移除
                        if not entryTrade.volume:
                            longTrade.pop(0)
                        
                        # 如果平仓交易已经全部清算，则退出循环
                        if not exitTrade.volume:
                            break
                        
                        # 如果平仓交易未全部清算，
                        if exitTrade.volume:
                            # 且开仓交易已经全部清算完，则平仓交易剩余的部分
                            # 等于新的反向开仓交易，添加到队列中
                            if not longTrade:
                                shortTrade.append(exitTrade)
                                break
                            # 如果开仓交易还有剩余，则进入下一轮循环
                            else:
                                pass                    
        
            
        for trade in longTrade:

            # 到最后交易日尚未平仓的交易，则以最后价格平仓
            symbol = trade.vtSymbol
            if self.mode == self.BAR_MODE:
                endPrice = self.bar[symbol].close
            else:
                endPrice = self.tick[symbol].lastPrice

            result = TradingResult(trade.price, trade.dt, endPrice, self.dt, 
                                   trade.volume, self.rate, self.slippage, self.size,
                                   self.slippageFunc, self.rateFunc)
            resultList.append(result)
            
        for trade in shortTrade:

            # 到最后交易日尚未平仓的交易，则以最后价格平仓
            symbol = trade.vtSymbol
            if self.mode == self.BAR_MODE:
                endPrice = self.bar[symbol].close
            else:
                endPrice = self.tick[symbol].lastPrice

            result = TradingResult(trade.price, trade.dt, endPrice, self.dt, 
                                   -trade.volume, self.rate, self.slippage, self.size,
                                   self.slippageFunc, self.rateFunc)
            resultList.append(result)            
        
        # 检查是否有交易
        if not resultList:
            self.output(u'无交易结果')
            return {}
        
        # 然后基于每笔交易的结果，我们可以计算具体的盈亏曲线和最大回撤等        
        capital = 0             # 资金
        maxCapital = 0          # 资金最高净值
        drawdown = 0            # 回撤
        
        totalResult = 0         # 总成交数量
        totalTurnover = 0       # 总成交金额（合约面值）
        totalCommission = 0     # 总手续费
        totalSlippage = 0       # 总滑点
        
        timeList = []           # 时间序列
        pnlList = []            # 每笔盈亏序列
        capitalList = []        # 盈亏汇总的时间序列
        drawdownList = []       # 回撤的时间序列
        
        winningResult = 0       # 盈利次数
        losingResult = 0        # 亏损次数		
        totalWinning = 0        # 总盈利金额		
        totalLosing = 0         # 总亏损金额        
        
        for result in resultList:
            capital += result.pnl
            maxCapital = max(capital, maxCapital)
            drawdown = capital - maxCapital
            
            pnlList.append(result.pnl)
            timeList.append(result.exitDt)      # 交易的时间戳使用平仓时间
            capitalList.append(capital)
            drawdownList.append(drawdown)
            
            totalResult += 1
            totalTurnover += result.turnover
            totalCommission += result.commission
            totalSlippage += result.slippage
            
            if result.pnl >= 0:
                winningResult += 1
                totalWinning += result.pnl
            else:
                losingResult += 1
                totalLosing += result.pnl
                
        # 计算盈亏相关数据
        winningRate = winningResult/totalResult*100         # 胜率
        
        averageWinning = 0                                  # 这里把数据都初始化为0
        averageLosing = 0
        profitLossRatio = 0
        
        if winningResult:
            averageWinning = totalWinning/winningResult     # 平均每笔盈利
        if losingResult:
            averageLosing = totalLosing/losingResult        # 平均每笔亏损
        if averageLosing:
            profitLossRatio = -averageWinning/averageLosing # 盈亏比

        # 返回回测结果
        d = {}
        d['capital'] = capital
        d['maxCapital'] = maxCapital
        d['drawdown'] = drawdown
        d['totalResult'] = totalResult
        d['totalTurnover'] = totalTurnover
        d['totalCommission'] = totalCommission
        d['totalSlippage'] = totalSlippage
        d['timeList'] = timeList
        d['pnlList'] = pnlList
        d['capitalList'] = capitalList
        d['drawdownList'] = drawdownList
        d['winningRate'] = winningRate
        d['averageWinning'] = averageWinning
        d['averageLosing'] = averageLosing
        d['profitLossRatio'] = profitLossRatio
        d['posList'] = posList
        d['tradeTimeList'] = tradeTimeList
        d['resultList'] = resultList
        
        return d
        
    #----------------------------------------------------------------------
    def showBacktestingResult(self, pdffile=None):
        """显示回测结果"""
        d = self.calculateBacktestingResult()
        
        # 输出
        self.output('-' * 30)
        self.output(u'第一笔交易：\t%s' % d['timeList'][0])
        self.output(u'最后一笔交易：\t%s' % d['timeList'][-1])
        
        self.output(u'总交易次数：\t%s' % formatNumber(d['totalResult']))        
        self.output(u'总盈亏：\t%s' % formatNumber(d['capital']))
        self.output(u'最大回撤: \t%s' % formatNumber(min(d['drawdownList'])))                
        
        self.output(u'平均每笔盈利：\t%s' %formatNumber(d['capital']/d['totalResult']))
        self.output(u'平均每笔滑点：\t%s' %formatNumber(d['totalSlippage']/d['totalResult']))
        self.output(u'平均每笔佣金：\t%s' %formatNumber(d['totalCommission']/d['totalResult']))
        
        self.output(u'胜率\t\t%s%%' %formatNumber(d['winningRate']))
        self.output(u'盈利交易平均值\t%s' %formatNumber(d['averageWinning']))
        self.output(u'亏损交易平均值\t%s' %formatNumber(d['averageLosing']))
        self.output(u'盈亏比：\t%s' %formatNumber(d['profitLossRatio']))
    
        # 绘图
        fig = self.get_new_figure(figsize=(10, 16))
        
        pCapital = plt.subplot(4, 1, 1)
        pCapital.set_ylabel("capital")
        pCapital.plot(d['capitalList'], color='r', lw=0.8)
        
        pDD = plt.subplot(4, 1, 2)
        pDD.set_ylabel("DD")
        pDD.bar(range(len(d['drawdownList'])), d['drawdownList'], color='g')
        
        pPnl = plt.subplot(4, 1, 3)
        pPnl.set_ylabel("pnl")
        pPnl.hist(d['pnlList'], bins=50, color='c')

        pPos = plt.subplot(4, 1, 4)
        pPos.set_ylabel("Position")
        if d['posList'][-1] == 0:
            del d['posList'][-1]
        tradeTimeIndex = [item.strftime("%m/%d %H:%M:%S") for item in d['tradeTimeList']]
        xindex = np.arange(0, len(tradeTimeIndex), np.int(len(tradeTimeIndex)/10))
        tradeTimeIndex = map(lambda i: tradeTimeIndex[i], xindex)
        pPos.plot(d['posList'], color='k', drawstyle='steps-pre')
        pPos.set_ylim(-1.2, 1.2)
        plt.sca(pPos)
        plt.tight_layout()
        plt.xticks(xindex, tradeTimeIndex, rotation=30)  # 旋转15

        if pdffile is None:        
            plt.show()
        else:
            txt = ""
            txt = txt + '-' * 30
            txt = txt + '\n' + u'first trade: %s' % d['timeList'][0]
            txt = txt + '\n' + u'last trade: %s' % d['timeList'][-1]
        
            txt = txt + '\n' + u'total trades: %s' % formatNumber(d['totalResult'])        
            txt = txt + '\n' + u'capital: %s' % formatNumber(d['capital'])
            txt = txt + '\n' + u'max drawdown: %s' % formatNumber(min(d['drawdownList']))
        
            txt = txt + '\n' + u'average profit each trade: %s' %formatNumber(d['capital']/d['totalResult'])
            txt = txt + '\n' + u'average slippage each trade: %s' %formatNumber(d['totalSlippage']/d['totalResult'])
            txt = txt + '\n' + u'average commision each trade: %s' %formatNumber(d['totalCommission']/d['totalResult'])
        
            txt = txt + '\n' + u'winning rate: %s%%' %formatNumber(d['winningRate'])
            txt = txt + '\n' + u'average winning: %s' %formatNumber(d['averageWinning'])
            txt = txt + '\n' + u'average losing: %s' %formatNumber(d['averageLosing'])
            txt = txt + '\n' + u'profit lost ratio: %s' %formatNumber(d['profitLossRatio'])
            pdffile.savefig(fig)

            secondPage = self.get_new_figure(figsize=(10, 16))
            secondPage.text(0.05,0.7, txt, transform=secondPage.transFigure, size=14, ha="left")
            pdffile.savefig(secondPage)

            pdffile.close()
        return d
    
    #----------------------------------------------------------------------
    def clearBacktestingResult(self):
        """清空之前回测的结果"""
        # 清空限价单相关
        self.limitOrderCount = 0
        self.limitOrderDict.clear()
        self.workingLimitOrderDict.clear()        
        
        # 清空停止单相关
        self.stopOrderCount = 0
        self.stopOrderDict.clear()
        self.workingStopOrderDict.clear()
        
        # 清空成交相关
        self.tradeCount = 0
        self.tradeDict.clear()
        
    #----------------------------------------------------------------------
    def runOptimization(self, strategyClass, optimizationSetting):
        """优化参数"""
        # 获取优化设置        
        settingList = optimizationSetting.generateSetting()
        targetName = optimizationSetting.optimizeTarget
        
        # 检查参数设置问题
        if not settingList or not targetName:
            self.output(u'优化设置有问题，请检查')
        
        # 遍历优化
        resultList = []
        for setting in settingList:
            self.clearBacktestingResult()
            self.output('-' * 30)
            self.output('setting: %s' %str(setting))
            self.initStrategy(strategyClass, setting)
            self.runBacktesting()
            df = self.calculateDailyResult()
            df, d = self.calculateDailyStatistics(df)            

            try:
                targetValue = d[targetName]
            except KeyError:
                targetValue = 0
            resultList.append(([str(setting)], targetValue, d))
        
        # 显示结果
        resultList.sort(reverse=True, key=lambda result:result[1])
        self.output('-' * 30)
        self.output(u'优化结果：')
        for result in resultList:
            self.output(u'参数：%s，目标：%s' %(result[0], result[1]))    
        return resultList
            
    #----------------------------------------------------------------------
    def runParallelOptimization(self, strategyClass, optimizationSetting):
        """并行优化参数"""
        # 获取优化设置        
        settingList = optimizationSetting.generateSetting()
        targetName = optimizationSetting.optimizeTarget
        
        # 检查参数设置问题
        if not settingList or not targetName:
            self.output(u'优化设置有问题，请检查')
        
        # 多进程优化，启动一个对应CPU核心数量的进程池
        pool = multiprocessing.Pool(multiprocessing.cpu_count())
        l = []

        for setting in settingList:
            l.append(pool.apply_async(optimize, (strategyClass, setting,
                                                 targetName, self.mode, 
                                                 self.startDate, self.initDays, self.endDate,
                                                 self.slippage, self.rate, self.size, self.priceTick,
                                                 self.dbName, self.symbol, self.slippageFunc,
                                                 self.rateFunc, self.capital)))
        pool.close()
        pool.join()

        resultList = []
        # 显示结果
        for res in l:
            try:
                resultList.append(res.get())
            except Exception as e:
                print str(e)

        resultList.sort(reverse=True, key=lambda result:result[1])
        self.output('-' * 30)
        self.output(u'优化结果：')
        for result in resultList:
            self.output(u'参数：%s，目标：%s' %(result[0], result[1]))    
            
        return resultList

    #----------------------------------------------------------------------
    def updateDailyClose(self, symbol, dt, price):
        """更新每日收盘价"""
        date = dt.date()
        if self.dailyResultDict.get(symbol) is None: 
            self.dailyResultDict[symbol] = OrderedDict()

        if date not in self.dailyResultDict[symbol]:
            self.dailyResultDict[symbol][date] = DailyResult(date, price)
        else:
            self.dailyResultDict[symbol][date].closePrice = price
            
    #----------------------------------------------------------------------

    def calculateDailyResult(self):
        dfs = []
        result = pd.DataFrame()
        for symbol, dl in self.dailyResultDict.items():
            df = self._calculateDailyResult(symbol, dl.copy())
            dfs.append(df)

        # tradeListDf = []
        for df in dfs:
            if result.empty:
                result = df
                # tradeListDf.append(result[["tradeList"]])    
                result = result.drop("tradeList", axis=1)
                continue

            # tradeListDf.append(df[["tradeList"]])    
            df = df.drop("tradeList", axis=1)
            result = result.add(df, fill_value=0)

        # tradeList = pd.concat(tradeListDf)
        return result #.merge(tradeList, left_index=True, right_index=True)


    def _calculateDailyResult(self, symbol, dailyResultDict):
        """计算按日统计的交易结果"""
        self.output(u'计算按日统计结果')
        
        # 将成交添加到每日交易结果中
        for trade in self.tradeDict.values():
            if trade.vtSymbol == symbol:
                date = trade.dt.date()
                dailyResult = dailyResultDict[date]
                dailyResult.addTrade(trade)
            
        # 遍历计算每日结果
        previousClose = 0
        openPosition = 0
        for dailyResult in dailyResultDict.values():
            dailyResult.previousClose = previousClose
            previousClose = dailyResult.closePrice
            
            dailyResult.calculatePnl(openPosition, self.size, self.rate, self.slippage,
                                     self.slippageFunc, self.rateFunc)
            openPosition = dailyResult.closePosition
            
        # 生成DataFrame
        resultDict = {k:[] for k in dailyResult.__dict__.keys()}
        for dailyResult in dailyResultDict.values():
            for k, v in dailyResult.__dict__.items():
                resultDict[k].append(v)
                
        resultDf = pd.DataFrame.from_dict(resultDict)
        
        # 计算衍生数据
        resultDf = resultDf.set_index('date')
        
        return resultDf
    
    #----------------------------------------------------------------------
    def calculateDailyStatistics(self, df):
        """计算按日统计的结果"""
        df['balance'] = df['netPnl'].cumsum() + self.capital
        df['return'] = (np.log(df['balance']) - np.log(df['balance'].shift(1))).fillna(0)
        df['highlevel'] = df['balance'].rolling(min_periods=1,window=len(df),center=False).max()
        df['drawdown'] = df['balance'] - df['highlevel']        
        df['ddPercent'] = df['drawdown'] / df['highlevel'] * 100
        
        # 计算统计结果
        startDate = df.index[0]
        endDate = df.index[-1]

        totalDays = len(df)
        profitDays = len(df[df['netPnl']>0])
        lossDays = len(df[df['netPnl']<0])
        
        endBalance = df['balance'].iloc[-1]
        maxDrawdown = df['drawdown'].min()
        tmp = df.reset_index()
        tmp["drawdown"].iloc[-1] = 0
        longestDrawdownDuration = tmp[tmp["drawdown"] == 0].index.to_series().diff().max()
        maxDdPercent = df['ddPercent'].min()
        
        totalNetPnl = df['netPnl'].sum()
        dailyNetPnl = totalNetPnl / totalDays
        
        totalCommission = df['commission'].sum()
        dailyCommission = totalCommission / totalDays
        
        totalSlippage = df['slippage'].sum()
        dailySlippage = totalSlippage / totalDays
        
        totalTurnover = df['turnover'].sum()
        dailyTurnover = totalTurnover / totalDays
        
        totalTradeCount = df['tradeCount'].sum()
        dailyTradeCount = totalTradeCount / totalDays
        
        totalReturn = (endBalance/self.capital - 1) * 100
        annualizedReturn = totalReturn / totalDays * 240

        meanReturnForKelly = df['positionPnlPercent'].dropna().mean() * 240  
        stdReturnForKelly = df['positionPnlPercent'].dropna().std() * math.sqrt(240) 
        meanExcessReturnForKelly = meanReturnForKelly - 0.04
        try:
            sharpeRatioExcessKelly = meanExcessReturnForKelly / stdReturnForKelly
        except:
            sharpeRatioExcessKelly = 0
        try:
            sharpeRatioKelly = meanReturnForKelly / stdReturnForKelly
        except:
            sharpeRatioKelly = 0
        try:
            kellyExcessF = meanExcessReturnForKelly / (stdReturnForKelly * stdReturnForKelly) 
        except:
            kellyExcessF = 0
        try:
            kellyF = meanReturnForKelly / (stdReturnForKelly * stdReturnForKelly) 
        except:
            kellyF = 0

        compoundedExcessLeveredRetrun = 0.04 + sharpeRatioExcessKelly*2 / 2
        compoundedLeveredRetrun = 0.04 + sharpeRatioKelly*2 / 2
        compoundedReturn = meanReturnForKelly - stdReturnForKelly*2 /2

        dailyReturn = df['return'].mean() * 100
        returnStd = df['return'].std() * 100
        
        if returnStd:
            sharpeRatio = dailyReturn / returnStd * np.sqrt(240)
        else:
            sharpeRatio = 0
            
        # 返回结果
        result = {
            'startDate': startDate,
            'endDate': endDate,
            'totalDays': totalDays,
            'profitDays': profitDays,
            'lossDays': lossDays,
            'endBalance': endBalance,
            'maxDrawdown': maxDrawdown,
            'longestDrawdownDuration': longestDrawdownDuration,
            'maxDdPercent': maxDdPercent,
            'totalNetPnl': totalNetPnl,
            'dailyNetPnl': dailyNetPnl,
            'totalCommission': totalCommission,
            'dailyCommission': dailyCommission,
            'totalSlippage': totalSlippage,
            'dailySlippage': dailySlippage,
            'totalTurnover': totalTurnover,
            'dailyTurnover': dailyTurnover,
            'totalTradeCount': totalTradeCount,
            'dailyTradeCount': dailyTradeCount,
            'totalReturn': totalReturn,
            'annualizedReturn': annualizedReturn,
            'dailyReturn': dailyReturn,
            'returnStd': returnStd,
            'sharpeRatio': sharpeRatio,
            'meanReturnForKelly': meanReturnForKelly,
            'stdReturnForKelly': stdReturnForKelly,
            'meanExcessReturnForKelly': meanExcessReturnForKelly,
            'sharpeRatioExcessKelly': sharpeRatioExcessKelly,  
            'sharpeRatioKelly': sharpeRatioKelly,
            'kellyExcessF': kellyExcessF,
            'kellyF': kellyF,
            'compoundedExcessLeveredRetrun': compoundedExcessLeveredRetrun,
            'compoundedLeveredRetrun': compoundedLeveredRetrun,
            'compoundedReturn': compoundedReturn

        }
        
        return df, result
    
    #----------------------------------------------------------------------
    def showDailyResult(self, df=None, result=None, savefig_path=None, main_contract=None):
        """显示按日统计的交易结果"""
        if df is None:
            df = self.calculateDailyResult()
            df, result = self.calculateDailyStatistics(df)
            
        # 输出统计结果
        self.output('-' * 30)
        self.output(u'首个交易日：\t%s' % result['startDate'])
        self.output(u'最后交易日：\t%s' % result['endDate'])
        
        self.output(u'总交易日：\t%s' % result['totalDays'])
        self.output(u'盈利交易日\t%s' % result['profitDays'])
        self.output(u'亏损交易日：\t%s' % result['lossDays'])
        
        self.output(u'起始资金：\t%s' % self.capital)
        self.output(u'结束资金：\t%s' % formatNumber(result['endBalance']))
    
        self.output(u'总收益率：\t%s%%' % formatNumber(result['totalReturn']))
        self.output(u'年化收益：\t%s%%' % formatNumber(result['annualizedReturn']))
        self.output(u'总盈亏：\t%s' % formatNumber(result['totalNetPnl']))
        self.output(u'最大回撤: \t%s' % formatNumber(result['maxDrawdown']))   
        self.output(u'Longest Drawdown Duration: \t%s' % formatNumber(result['longestDrawdownDuration']))   
        self.output(u'百分比最大回撤: %s%%' % formatNumber(result['maxDdPercent']))   
        
        self.output(u'总手续费：\t%s' % formatNumber(result['totalCommission']))
        self.output(u'总滑点：\t%s' % formatNumber(result['totalSlippage']))
        self.output(u'总成交金额：\t%s' % formatNumber(result['totalTurnover']))
        self.output(u'总成交笔数：\t%s' % formatNumber(result['totalTradeCount']))
        
        self.output(u'日均盈亏：\t%s' % formatNumber(result['dailyNetPnl']))
        self.output(u'日均手续费：\t%s' % formatNumber(result['dailyCommission']))
        self.output(u'日均滑点：\t%s' % formatNumber(result['dailySlippage']))
        self.output(u'日均成交金额：\t%s' % formatNumber(result['dailyTurnover']))
        self.output(u'日均成交笔数：\t%s' % formatNumber(result['dailyTradeCount']))
        
        self.output(u'日均收益率：\t%s%%' % formatNumber(result['dailyReturn']))
        self.output(u'收益标准差：\t%s%%' % formatNumber(result['returnStd']))
        self.output(u'Sharpe Ratio：\t%s' % formatNumber(result['sharpeRatio']))

        self.output(u'Kelly Annual Mean Return：\t%s%%' % formatNumber(result['meanReturnForKelly']*100))
        self.output(u'Kelly STD Return：\t%s%%' % formatNumber(result['stdReturnForKelly']*100))
        self.output(u'Kelly Mean Excess Return：\t%s%%' % formatNumber(result['meanExcessReturnForKelly']*100))
        self.output(u'Kelly Excess SharpeRatio：\t%s' % formatNumber(result['sharpeRatioExcessKelly']))
        self.output(u'Kelly SharpeRatio：\t%s' % formatNumber(result['sharpeRatioKelly']))
        self.output(u'Kelly Leverage on excess return ：\t%s' % formatNumber(result['kellyExcessF']))
        self.output(u'Kelly Leverage on return ：\t%s' % formatNumber(result['kellyF']))
        self.output(u'Kelly Compounded Levered on excess return ：\t%s%%' % formatNumber(result['compoundedExcessLeveredRetrun']*100))
        self.output(u'Kelly Compounded Levered return ：\t%s%%' % formatNumber(result['compoundedLeveredRetrun']*100))
        self.output(u'Kelly Compounded NO Levered return ：\t%s%%' % formatNumber(result['compoundedReturn']*100))
        
        # 绘图
        fig = self.get_new_figure(figsize=(10, 16))
        fig_num = 4
        refer_num = 1
        if main_contract is not None:
            fig_num = 5
        pBalance = plt.subplot(fig_num, 1, refer_num)
        pBalance.set_title('Balance')
        df['balance'].plot(legend=True)
        refer_num = refer_num + 1
        if main_contract is not None:
            pClose = plt.subplot(fig_num, 1, refer_num)
            pClose.set_title('Asset Under Test')
            df['balance'].plot(legend=True)
            main_contract = main_contract[main_contract.index >= pd.to_datetime(result['startDate'])]
            main_contract = main_contract[main_contract.index <= pd.to_datetime(result['endDate'])]
            main_contract["close"].plot(color="red", secondary_y=True, legend=True)
            refer_num = refer_num + 1
        
        pDrawdown = plt.subplot(fig_num, 1, refer_num)
        pDrawdown.set_title('Drawdown')
        pDrawdown.fill_between(range(len(df)), df['drawdown'].values)
        refer_num = refer_num + 1
        
        pPnl = plt.subplot(fig_num, 1, refer_num)
        pPnl.set_title('Daily Pnl') 
        df['netPnl'].plot(kind='bar', legend=False, grid=False, xticks=[])
        refer_num = refer_num + 1

        pKDE = plt.subplot(fig_num, 1, refer_num)
        pKDE.set_title('Daily Pnl Distribution')
        df['netPnl'].hist(bins=50)

        if savefig_path is None:  
            plt.show()
        else:
            from matplotlib.backends.backend_pdf import PdfPages
            txt = ""
            txt = txt + '-' * 30
            txt = txt + '\n' + u'start date: %s' % result['startDate']
            txt = txt + '\n' + u'end date: %s' % result['endDate']
        
            txt = txt + '\n' + u'total trade days: %s' % result['totalDays']
            txt = txt + '\n' + u'profit days: %s' % result['profitDays']
            txt = txt + '\n' + u'loss days: %s' % result['lossDays']
        
            txt = txt + '\n' + u'start capital: %s' % self.capital
            txt = txt + '\n' + u'end balance: %s' % formatNumber(result['endBalance'])
    
            txt = txt + '\n' + u'total retrun: %s%%' % formatNumber(result['totalReturn'])
            txt = txt + '\n' + u'annualize return: %s%%' % formatNumber(result['annualizedReturn'])
            txt = txt + '\n' + u'total net pnl: %s' % formatNumber(result['totalNetPnl'])
            txt = txt + '\n' + u'max drawdown: %s' % formatNumber(result['maxDrawdown'])   
            txt = txt + '\n' + u'longest drawdown duration: %s' % formatNumber(result['longestDrawdownDuration'])   

            txt = txt + '\n' + u'max drawdown percent: %s%%' % formatNumber(result['maxDdPercent'])   
        
            txt = txt + '\n' + u'total commision: %s' % formatNumber(result['totalCommission'])
            txt = txt + '\n' + u'total slippage: %s' % formatNumber(result['totalSlippage'])
            txt = txt + '\n' + u'total turnover: %s' % formatNumber(result['totalTurnover'])
            txt = txt + '\n' + u'total trade count: %s' % formatNumber(result['totalTradeCount'])
        
            txt = txt + '\n' + u'daily net pnl: %s' % formatNumber(result['dailyNetPnl'])
            txt = txt + '\n' + u'dayly commission: %s' % formatNumber(result['dailyCommission'])
            txt = txt + '\n' + u'daily slippage: %s' % formatNumber(result['dailySlippage'])
            txt = txt + '\n' + u'daily turnover: %s' % formatNumber(result['dailyTurnover'])
            txt = txt + '\n' + u'daily trade count: %s' % formatNumber(result['dailyTradeCount'])
        
            txt = txt + '\n' + u'daily return: %s%%' % formatNumber(result['dailyReturn'])
            txt = txt + '\n' + u'return std: %s%%' % formatNumber(result['returnStd'])
            txt = txt + '\n' + u'Sharpe Ratio: %s' % formatNumber(result['sharpeRatio'])

            txt = txt + '\n' + u'Kelly Annual Mean Return: %s%%' % formatNumber(result['meanReturnForKelly']*100)
            txt = txt + '\n' + u'Kelly STD Return: %s%%' % formatNumber(result['stdReturnForKelly']*100)
            txt = txt + '\n' + u'Kelly Mean Excess Return: %s%%' % formatNumber(result['meanExcessReturnForKelly']*100)
            txt = txt + '\n' + u'Kelly Excess SharpeRatio: %s' % formatNumber(result['sharpeRatioExcessKelly'])
            txt = txt + '\n' + u'Kelly SharpeRatio: %s' % formatNumber(result['sharpeRatioKelly'])
            txt = txt + '\n' + u'Kelly Leverage on excess return: %s' % formatNumber(result['kellyExcessF'])
            txt = txt + '\n' + u'Kelly Leverage on return: %s' % formatNumber(result['kellyF'])
            txt = txt + '\n' + u'Kelly Compounded Levered on excess return: %s%%' % formatNumber(result['compoundedExcessLeveredRetrun']*100)
            txt = txt + '\n' + u'Kelly Compounded Levered return: %s%%' % formatNumber(result['compoundedLeveredRetrun']*100)
            txt = txt + '\n' + u'Kelly Compounded NO Levered return: %s%%' % formatNumber(result['compoundedReturn']*100)
            plt.subplots_adjust(bottom = 0.1)
            pdffile = PdfPages(savefig_path.replace(".pdf", "_sharpe_%s_mdd_%s_mddp_%s_ldd_%s" % (formatNumber(result["sharpeRatio"]),
                                                                                                 formatNumber(result['maxDrawdown']),
                                                                                                 formatNumber(result['maxDdPercent']),
                                                                                                 formatNumber(result['longestDrawdownDuration'])))+".pdf")

            pdffile.savefig(fig)

            secondPage = self.get_new_figure(figsize=(10, 16))
            secondPage.text(0.05,0.5, txt, transform=secondPage.transFigure, size=14, ha="left")
            pdffile.savefig(secondPage)

        return df, result, pdffile if savefig_path is not None else None
       
        
########################################################################
class TradingResult(object):
    """每笔交易的结果"""

    #----------------------------------------------------------------------
    def __init__(self, entryPrice, entryDt, exitPrice, 
                 exitDt, volume, rate, slippage, size, slippageFunc=None,
                 rateFunc=None):
        """Constructor"""
        self.entryPrice = entryPrice    # 开仓价格
        self.exitPrice = exitPrice      # 平仓价格
        
        self.entryDt = entryDt          # 开仓时间datetime    
        self.exitDt = exitDt            # 平仓时间
        
        self.volume = volume    # 交易数量（+/-代表方向）
        
        self.turnover = (self.entryPrice+self.exitPrice)*size*abs(volume)   # 成交金额
        if rateFunc is None:
            self.commission = self.turnover*rate                                # 手续费成本
        else:
            self.commission = rateFunc(self.turnover, rate)

        if slippageFunc is None:
            self.slippage = slippage*2*size*abs(volume)                         # 滑点成本
        else:
            self.slippage = slippageFunc(slippage, size, volume)

        self.pnl = ((self.exitPrice - self.entryPrice) * volume * size 
                    - self.commission - self.slippage)                      # 净盈亏


########################################################################
class DailyResult(object):
    """每日交易的结果"""

    #----------------------------------------------------------------------
    def __init__(self, date, closePrice):
        """Constructor"""
        self.date = date                # 日期
        self.closePrice = closePrice    # 当日收盘价
        self.previousClose = 0          # 昨日收盘价
        
        self.tradeList = []             # 成交列表
        self.tradeCount = 0             # 成交数量
        
        self.openPosition = 0           # 开盘时的持仓
        self.closePosition = 0          # 收盘时的持仓
        
        self.tradingPnl = 0             # 交易盈亏
        self.positionPnl = 0            # 持仓盈亏
        self.totalPnl = 0               # 总盈亏
        
        self.turnover = 0               # 成交量
        self.commission = 0             # 手续费
        self.slippage = 0               # 滑点
        self.netPnl = 0                 # 净盈亏
        self.netPnlPercent = 0                 # 净盈亏
        self.positionPnlPercent = 0                 # 净盈亏
        
    #----------------------------------------------------------------------
    def addTrade(self, trade):
        """添加交易"""
        self.tradeList.append(trade)

    #----------------------------------------------------------------------
    def calculatePnl(self, openPosition=0, size=1, rate=0, slippage=0,
                     slippageFunc=None, rateFunc=None):
        # 持仓部分
        self.openPosition = openPosition
        self.positionPnl = self.openPosition * (self.closePrice - self.previousClose) * size
        self.positionPnlPercent = self.openPosition * (np.log(self.closePrice) - np.log(self.previousClose))
        self.closePosition = self.openPosition
        
        # 交易部分
        self.tradeCount = len(self.tradeList)
        
        for trade in self.tradeList:
            if trade.direction == DIRECTION_LONG:
                posChange = trade.volume
            else:
                posChange = -trade.volume
                
            self.tradingPnl += posChange * (self.closePrice - trade.price) * size
            self.closePosition += posChange
            self.turnover += trade.price * trade.volume * size
            if rateFunc is None:
                self.commission += trade.price * trade.volume * size * rate
                tradePnlPercent = posChange * (np.log(self.closePrice * size - trade.price * trade.volume * size * rate) - np.log(trade.price * size))
                self.positionPnlPercent = self.positionPnlPercent + tradePnlPercent
            else:
                self.commission += rateFunc(trade.price * trade.volume * size, rate)
                tradePnlPercent = posChange * (np.log(self.closePrice * size - rateFunc(trade.price * trade.volume * size, rate)) - np.log(trade.price * size))
                self.positionPnlPercent = self.positionPnlPercent + tradePnlPercent
            if slippageFunc is None:
                self.slippage += trade.volume * size * slippage
                tradePnlPercent = posChange * (np.log(self.closePrice * size - trade.volume * size * slippage) - np.log(trade.price * size))
                self.positionPnlPercent = self.positionPnlPercent + tradePnlPercent
            else:
                self.slippage += slippageFunc(slippage, size, trade.volume)
                tradePnlPercent = posChange * (np.log(self.closePrice * size - slippageFunc(slippage, size, trade.volume)) - np.log(trade.price * size))
                self.positionPnlPercent = self.positionPnlPercent + tradePnlPercent
        
        # 汇总
        self.totalPnl = self.tradingPnl + self.positionPnl
        self.netPnl = self.totalPnl - self.commission - self.slippage
        self.netPnlPercent = self.netPnlPercent + self.positionPnlPercent


########################################################################
class OptimizationSetting(object):
    """优化设置"""

    #----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        self.paramDict = OrderedDict()
        
        self.optimizeTarget = ''        # 优化目标字段
        
    #----------------------------------------------------------------------
    def addParameter(self, name, start, end=None, step=None):
        """增加优化参数"""
        if end is None and step is None:
            self.paramDict[name] = [start]
            return 
        
        if end < start:
            print u'参数起始点必须不大于终止点'
            return
        
        if step <= 0:
            print u'参数布进必须大于0'
            return
        
        l = []
        param = start
        
        while param <= end:
            l.append(param)
            param += step
        
        self.paramDict[name] = l
        
    #----------------------------------------------------------------------
    def generateSetting(self):
        """生成优化参数组合"""
        # 参数名的列表
        nameList = self.paramDict.keys()
        paramList = self.paramDict.values()
        
        # 使用迭代工具生产参数对组合
        productList = list(product(*paramList))
        
        # 把参数对组合打包到一个个字典组成的列表中
        settingList = []
        for p in productList:
            d = dict(zip(nameList, p))
            settingList.append(d)
    
        return settingList
    
    #----------------------------------------------------------------------
    def setOptimizeTarget(self, target):
        """设置优化目标字段"""
        self.optimizeTarget = target


#----------------------------------------------------------------------
def formatNumber(n):
    """格式化数字到字符串"""
    rn = round(n, 2)        # 保留两位小数
    return format(rn, ',')  # 加上千分符
    

#----------------------------------------------------------------------
def optimize(strategyClass, setting, targetName,
             mode, startDate, initDays, endDate,
             slippage, rate, size, priceTick,
             dbName, symbol, slippageFunc, rateFunc, capital):
    """多进程优化时跑在每个进程中运行的函数"""
    engine = BacktestingEngine()
    engine.setCapital(capital)
    engine.setBacktestingMode(mode)
    engine.setStartDate(startDate, initDays)
    engine.setEndDate(endDate)
    engine.setSlippage(slippage, slippageFunc)
    engine.setRate(rate, rateFunc)
    engine.setSize(size)
    engine.setPriceTick(priceTick)
    engine.setDatabase(dbName, symbol)
    try: 
        engine.initStrategy(strategyClass, setting)
        engine.runBacktesting()
    except:
        print setting
        raise
    
    df = engine.calculateDailyResult()
    df, d = engine.calculateDailyStatistics(df)

    try:
        targetValue = d[targetName]
    except KeyError:
        targetValue = 0            

    return (str(setting), targetValue, d)    
