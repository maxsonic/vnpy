# encoding: UTF-8

'''
本文件包含了CTA引擎中的策略开发用模板，开发策略时需要继承CtaTemplate类。
'''

import numpy as np
import pandas as pd
import talib

from vnpy.trader.vtConstant import *
from vnpy.trader.vtObject import VtBarData

from .ctaBase import *


########################################################################
class CtaTemplate(object):
    """CTA策略模板"""
    
    # 策略类的名称和作者
    className = 'CtaTemplate'
    author = EMPTY_UNICODE
    
    # MongoDB数据库的名称，K线数据库默认为1分钟
    tickDbName = TICK_DB_NAME
    barDbName = MINUTE_DB_NAME
    
    # 策略的基本参数
    name = EMPTY_UNICODE           # 策略实例名称
    vtSymbol = EMPTY_STRING        # 交易的合约vt系统代码    
    productClass = EMPTY_STRING    # 产品类型（只有IB接口需要）
    currency = EMPTY_STRING        # 货币（只有IB接口需要）
    
    # 策略的基本变量，由引擎管理
    inited = False                 # 是否进行了初始化
    trading = False                # 是否启动交易，由引擎管理
    pos = EMPTY_INT                      # 持仓情况
    
    # 参数列表，保存了参数的名称
    paramList = ['name',
                 'className',
                 'author',
                 'vtSymbol']
    
    # 变量列表，保存了变量的名称
    varList = ['inited',
               'trading',
               'pos']
    
    # 同步列表，保存了需要保存到数据库的变量名称
    syncList = ['pos']

    #----------------------------------------------------------------------
    def __init__(self, ctaEngine, setting):
        """Constructor"""
        self.ctaEngine = ctaEngine
        self.pos = dict()

        # 设置策略的参数
        if setting:
            d = self.__dict__
            for key in self.paramList:
                if key in setting:
                    d[key] = setting[key]
    
    #----------------------------------------------------------------------
    def onInit(self):
        """初始化策略（必须由用户继承实现）"""
        raise NotImplementedError
    
    #----------------------------------------------------------------------
    def onStart(self):
        """启动策略（必须由用户继承实现）"""
        raise NotImplementedError
    
    #----------------------------------------------------------------------
    def onStop(self):
        """停止策略（必须由用户继承实现）"""
        raise NotImplementedError

    #----------------------------------------------------------------------
    def onTick(self, tick):
        """收到行情TICK推送（必须由用户继承实现）"""
        raise NotImplementedError

    #----------------------------------------------------------------------
    def onOrder(self, order):
        """收到委托变化推送（必须由用户继承实现）"""
        raise NotImplementedError
    
    #----------------------------------------------------------------------
    def onTrade(self, trade):
        """收到成交推送（必须由用户继承实现）"""
        raise NotImplementedError
    
    #----------------------------------------------------------------------
    def onBar(self, bar):
        """收到Bar推送（必须由用户继承实现）"""
        raise NotImplementedError
    
    #----------------------------------------------------------------------
    def onStopOrder(self, so):
        """收到停止单推送（必须由用户继承实现）"""
        raise NotImplementedError
    
    #----------------------------------------------------------------------
    def buy(self, symbol, price, volume, stop=False):
        """买开"""
        return self.sendOrder(symbol, CTAORDER_BUY, price, volume, stop)
    
    #----------------------------------------------------------------------
    def sell(self, symbol, price, volume, stop=False):
        """卖平"""
        return self.sendOrder(symbol, CTAORDER_SELL, price, volume, stop)       

    #----------------------------------------------------------------------
    def short(self, symbol, price, volume, stop=False):
        """卖开"""
        return self.sendOrder(symbol, CTAORDER_SHORT, price, volume, stop)          
 
    #----------------------------------------------------------------------
    def cover(self, symbol, price, volume, stop=False):
        """买平"""
        return self.sendOrder(symbol, CTAORDER_COVER, price, volume, stop)
        
    #----------------------------------------------------------------------
    def sendOrder(self, symbol, orderType, price, volume, stop=False):
        """发送委托"""
        if self.trading:
            # 如果stop为True，则意味着发本地停止单
            if stop:
                vtOrderIDList = self.ctaEngine.sendStopOrder(symbol, orderType, price, volume, self)
            else:
                vtOrderIDList = self.ctaEngine.sendOrder(symbol, orderType, price, volume, self) 
            return vtOrderIDList
        else:
            # 交易停止时发单返回空字符串
            return []
        
    #----------------------------------------------------------------------
    def cancelOrder(self, vtOrderID):
        """撤单"""
        # 如果发单号为空字符串，则不进行后续操作
        if not vtOrderID:
            return
        
        if STOPORDERPREFIX in vtOrderID:
            self.ctaEngine.cancelStopOrder(vtOrderID)
        else:
            self.ctaEngine.cancelOrder(vtOrderID)
            
    #----------------------------------------------------------------------
    def cancelAll(self):
        """全部撤单"""
        self.ctaEngine.cancelAll(self.name)
    
    #----------------------------------------------------------------------
    def insertTick(self, tick):
        """向数据库中插入tick数据"""
        self.ctaEngine.insertData(self.tickDbName, self.vtSymbol, tick)
    
    #----------------------------------------------------------------------
    def insertBar(self, bar):
        """向数据库中插入bar数据"""
        self.ctaEngine.insertData(self.barDbName, self.vtSymbol, bar)
        
    #----------------------------------------------------------------------
    def loadTick(self, days):
        """读取tick数据"""
        return self.ctaEngine.loadTick(self.tickDbName, self.vtSymbol, days)
    
    #----------------------------------------------------------------------
    def loadBar(self, days, symbolMap):
        """
        symbolMap is to map the current symbol with history data
        {
            "targetSymbolInBar": "historySymbol"
        }
        读取bar数据"""
        return self.ctaEngine.loadBar(self.barDbName, symbolMap, days)
    
    #----------------------------------------------------------------------
    def writeCtaLog(self, content):
        """记录CTA日志"""
        content = self.name + ':' + content
        self.ctaEngine.writeCtaLog(content)
        
    #----------------------------------------------------------------------
    def putEvent(self):
        """发出策略状态变化事件"""
        self.ctaEngine.putStrategyEvent(self.name)
        
    #----------------------------------------------------------------------
    def getEngineType(self):
        """查询当前运行的环境"""
        return self.ctaEngine.engineType
    
    #----------------------------------------------------------------------
    def saveSyncData(self):
        """保存同步数据到数据库"""
        if self.trading:
            self.ctaEngine.saveSyncData(self)
    

########################################################################
class TargetPosTemplate(CtaTemplate):
    """
    允许直接通过修改目标持仓来实现交易的策略模板
    
    开发策略时，无需再调用buy/sell/cover/short这些具体的委托指令，
    只需在策略逻辑运行完成后调用setTargetPos设置目标持仓，底层算法
    会自动完成相关交易，适合不擅长管理交易挂撤单细节的用户。    
    
    使用该模板开发策略时，请在以下回调方法中先调用母类的方法：
    onTick
    onBar
    onOrder
    
    假设策略名为TestStrategy，请在onTick回调中加上：
    super(TestStrategy, self).onTick(tick)
    
    其他方法类同。
    """
    
    className = 'TargetPosTemplate'
    author = u'量衍投资'
    
    # 目标持仓模板的基本变量
    tickAdd = 1             # 委托时相对基准价格的超价
    lastTick = None         # 最新tick数据
    lastBar = None          # 最新bar数据
    targetPos = EMPTY_INT   # 目标持仓
    orderList = []          # 委托号列表

    # 变量列表，保存了变量的名称
    varList = ['inited',
               'trading',
               'pos',
               'targetPos']

    #----------------------------------------------------------------------
    def __init__(self, ctaEngine, setting):
        """Constructor"""
        super(TargetPosTemplate, self).__init__(ctaEngine, setting)
        
    #----------------------------------------------------------------------
    def onTick(self, tick):
        """收到行情推送"""
        self.lastTick = tick
        
        # 实盘模式下，启动交易后，需要根据tick的实时推送执行自动开平仓操作
        if self.trading:
            self.trade()
        
    #----------------------------------------------------------------------
    def onBar(self, bar):
        """收到K线推送"""
        self.lastBar = bar
    
    #----------------------------------------------------------------------
    def onOrder(self, order):
        """收到委托推送"""
        if order.status == STATUS_ALLTRADED or order.status == STATUS_CANCELLED:
            if order.vtOrderID in self.orderList:
                self.orderList.remove(order.vtOrderID)
    
    #----------------------------------------------------------------------
    def setTargetPos(self, targetPos):
        """设置目标仓位"""
        self.targetPos = targetPos
        
        self.trade()
        
    #----------------------------------------------------------------------
    def trade(self):
        """执行交易"""
        # 先撤销之前的委托
        self.cancelAll()
        
        # 如果目标仓位和实际仓位一致，则不进行任何操作
        posChange = self.targetPos - self.pos
        if not posChange:
            return
        
        # 确定委托基准价格，有tick数据时优先使用，否则使用bar
        longPrice = 0
        shortPrice = 0
        
        if self.lastTick:
            if posChange > 0:
                longPrice = self.lastTick.askPrice1 + self.tickAdd
                if tick.upperLimit:
                    longPrice = min(longPrice, tick.upperLimit)         # 涨停价检查
            else:
                shortPrice = self.lastTick.bidPrice1 - self.tickAdd
                if tick.lowerLimit:
                    shortPrice = max(shortPrice, tick.lowerLimit)       # 跌停价检查
        else:
            if posChange > 0:
                longPrice = self.lastBar.close + self.tickAdd
            else:
                shortPrice = self.lastBar.close - self.tickAdd
        
        # 回测模式下，采用合并平仓和反向开仓委托的方式
        if self.getEngineType() == ENGINETYPE_BACKTESTING:
            if posChange > 0:
                l = self.buy(longPrice, abs(posChange))
            else:
                l = self.short(shortPrice, abs(posChange))
            self.orderList.extend(l)
        
        # 实盘模式下，首先确保之前的委托都已经结束（全成、撤销）
        # 然后先发平仓委托，等待成交后，再发送新的开仓委托
        else:
            # 检查之前委托都已结束
            if self.orderList:
                return
            
            # 买入
            if posChange > 0:
                # 若当前有空头持仓
                if self.pos < 0:
                    # 若买入量小于空头持仓，则直接平空买入量
                    if posChange < abs(self.pos):
                        l = self.cover(longPrice, posChange)
                    # 否则先平所有的空头仓位
                    else:
                        l = self.cover(longPrice, abs(self.pos))
                # 若没有空头持仓，则执行开仓操作
                else:
                    l = self.buy(longPrice, abs(posChange))
            # 卖出和以上相反
            else:
                if self.pos > 0:
                    if abs(posChange) < self.pos:
                        l = self.sell(shortPrice, abs(posChange))
                    else:
                        l = self.sell(shortPrice, abs(self.pos))
                else:
                    l = self.short(shortPrice, abs(posChange))
            self.orderList.extend(l)
    
    
########################################################################
class BarGenerator(object):
    """
    K线合成器，支持：
    1. 基于Tick合成1分钟K线
    2. 基于1分钟K线合成X分钟K线（X可以是2、3、5、10、15、30	）
    """

    #----------------------------------------------------------------------
    def __init__(self, onBar, xmin=0, onXminBar=None):
        """Constructor"""
        self.bar = None             # 1分钟K线对象
        self.onBar = onBar          # 1分钟K线回调函数
        
        self.xminBar = dict()         # X分钟K线对象
        self.xmin = xmin            # X的值
        self.onXminBar = onXminBar  # X分钟K线的回调函数
        
        self.lastTick = dict()       # 上一TICK缓存对象
        
    #----------------------------------------------------------------------
    def updateTick(self, tick):
        """TICK更新"""
        newMinute = False   # 默认不是新的一分钟
        symbol = tick.vtSymbol
        
        # 尚未创建对象
        if not self.bar:
            # self.bar = VtBarData()
            self.bar = dict() # ad dict to VtBarData object
            self.bar[symbol] = VtBarData()
            newMinute = True
        # 新的一分钟
        elif self.bar.get(symbol) is None:
            self.bar[symbol] = VtBarData()
            newMinute = True
        elif self.bar[symbol].datetime.minute != tick.datetime.minute:
            # 生成上一分钟K线的时间戳
            if self.bar.get(symbol) is None:
                self.bar[symbol] = VtBarData()
            self.bar[symbol].datetime = self.bar[symbol].datetime.replace(second=0, microsecond=0)  # 将秒和微秒设为0
            self.bar[symbol].date = self.bar[symbol].datetime.strftime('%Y%m%d')
            self.bar[symbol].time = self.bar[symbol].datetime.strftime('%H:%M:%S.%f')
            
            # 推送已经结束的上一分钟K线
            self.onBar(self.bar)
            
            # 创建新的K线对象
            # self.bar = VtBarData()
            self.bar = dict()
            newMinute = True
            
        # 初始化新一分钟的K线数据
        if newMinute:
            if self.bar.get(symbol) is None:
                self.bar[symbol] = VtBarData()
            self.bar[symbol].vtSymbol = tick.vtSymbol
            self.bar[symbol].symbol = tick.symbol
            self.bar[symbol].exchange = tick.exchange

            self.bar[symbol].open = tick.lastPrice
            self.bar[symbol].high = tick.lastPrice
            self.bar[symbol].low = tick.lastPrice
        # 累加更新老一分钟的K线数据
        else:                                   
            self.bar[symbol].high = max(self.bar[symbol].high, tick.lastPrice)
            self.bar[symbol].low = min(self.bar[symbol].low, tick.lastPrice)

        # 通用更新部分
        self.bar[symbol].close = tick.lastPrice        
        self.bar[symbol].datetime = tick.datetime  
        self.bar[symbol].openInterest = tick.openInterest
        lastTick = self.lastTick.get(symbol) 
        if lastTick is not None:
            volumeChange = (tick.volume - lastTick.volume) # 当前K线内的成交量
            self.bar[symbol].volume += max(volumeChange, 0) # 当前K线内的成交量
            
        # 缓存Tick
        self.lastTick[symbol] = tick

    #----------------------------------------------------------------------
    def updateBar(self, bar):
        """
        bar is a dict here, symbol paired with bar data
        so xminBar here should be a dict too
        1分钟K线更新
        """
        # 尚未创建对象
        if self.xminBar is None:
            self.xminBar = dict()
        for symbol, b in bar.items():
            if self.xminBar.get(symbol) is None:
                self.xminBar[symbol] = VtBarData()
            
                self.xminBar[symbol].vtSymbol = b.vtSymbol
                self.xminBar[symbol].symbol = b.symbol
                self.xminBar[symbol].exchange = b.exchange
        
                self.xminBar[symbol].open = b.open
                self.xminBar[symbol].high = b.high
                self.xminBar[symbol].low = b.low            
                
                self.xminBar[symbol].datetime = b.datetime    # 以第一根分钟K线的开始时间戳作为X分钟线的时间戳
            # 累加老K线
            else:
                self.xminBar[symbol].high = max(self.xminBar[symbol].high, b.high)
                self.xminBar[symbol].low = min(self.xminBar[symbol].low, b.low)
    
        # 通用部分
        for symbol, b in bar.items():
            self.xminBar[symbol].close = b.close        
            self.xminBar[symbol].openInterest = b.openInterest
            self.xminBar[symbol].volume += int(float(b.volume))                
            
        # X分钟已经走完
        anySymbol = bar.keys()[0]
        if not (bar[anySymbol].datetime.minute + 1) % self.xmin:   # 可以用X整除
            # 生成上一X分钟K线的时间戳
            for symbol, b in bar.items():
                self.xminBar[symbol].datetime = self.xminBar[symbol].datetime.replace(second=0, microsecond=0)  # 将秒和微秒设为0
                self.xminBar[symbol].date = self.xminBar[symbol].datetime.strftime('%Y%m%d')
                self.xminBar[symbol].time = self.xminBar[symbol].datetime.strftime('%H:%M:%S.%f')
            
            # 推送
            self.onXminBar(self.xminBar)
            
            # 清空老K线缓存对象
            self.xminBar = None


########################################################################
class ArrayManager(object):
    """
    K线序列管理工具，负责：
    1. K线时间序列的维护
    2. 常用技术指标的计算
    """

    #----------------------------------------------------------------------
    def __init__(self, size=100, symbol=""):
        """Constructor"""
        self.count = 0                      # 缓存计数
        self.size = size                    # 缓存大小
        self.inited = False                 # True if count>=size
        self.symbol = symbol
        
        self.openArray = np.zeros(size)     # OHLC
        self.highArray = np.zeros(size)
        self.lowArray = np.zeros(size)
        self.closeArray = np.zeros(size)
        self.volumeArray = np.zeros(size)
        
    #----------------------------------------------------------------------
    def updateBar(self, bar):
        """更新K线"""
        self.count += 1
        if not self.inited and self.count >= self.size:
            self.inited = True

        self.openArray[0:self.size-1] = self.openArray[1:self.size]
        self.highArray[0:self.size-1] = self.highArray[1:self.size]
        self.lowArray[0:self.size-1] = self.lowArray[1:self.size]
        self.closeArray[0:self.size-1] = self.closeArray[1:self.size]
        self.volumeArray[0:self.size-1] = self.volumeArray[1:self.size]
    
        self.openArray[-1] = bar.open
        self.highArray[-1] = bar.high
        self.lowArray[-1] = bar.low        
        self.closeArray[-1] = bar.close
        self.volumeArray[-1] = bar.volume
        
    #----------------------------------------------------------------------
    @property
    def open(self):
        """获取开盘价序列"""
        return self.openArray
        
    #----------------------------------------------------------------------
    @property
    def high(self):
        """获取最高价序列"""
        return self.highArray
    
    #----------------------------------------------------------------------
    @property
    def low(self):
        """获取最低价序列"""
        return self.lowArray
    
    #----------------------------------------------------------------------
    @property
    def close(self):
        """获取收盘价序列"""
        return self.closeArray
    
    #----------------------------------------------------------------------
    @property    
    def volume(self):
        """获取成交量序列"""
        return self.volumeArray
    
    #----------------------------------------------------------------------
    def sma(self, n, array=False):
        """简单均线"""
        result = talib.SMA(self.close, n)
        if array:
            return result
        return result[-1]
        
    #----------------------------------------------------------------------
    def std(self, n, array=False):
        """标准差"""
        result = talib.STDDEV(self.close, n)
        if array:
            return result
        return result[-1]
    
    #----------------------------------------------------------------------
    def cci(self, n, array=False):
        """CCI指标"""
        result = talib.CCI(self.high, self.low, self.close, n)
        if array:
            return result
        return result[-1]
        
    #----------------------------------------------------------------------
    def atr(self, n, array=False):
        """ATR指标"""
        result = talib.ATR(self.high, self.low, self.close, timeperiod=n)
        if array:
            return result
        return result[-1]
        
    #----------------------------------------------------------------------
    def rsi(self, n, array=False):
        """RSI指标"""
        result = talib.RSI(self.close, n)
        if array:
            return result
        return result[-1]
    
    # #----------------------------------------------------------------------
    # def macd(self, fastPeriod, slowPeriod, signalPeriod, array=False):
    #     """MACD指标"""
    #     macd, signal, hist = talib.MACD(self.close, fastPeriod,
    #                                     slowPeriod, signalPeriod)
    #     if array:
    #         return macd, signal, hist
    #     return macd[-1], signal[-1], hist[-1]
    
    #----------------------------------------------------------------------
    def adx(self, n, array=False):
        """ADX指标"""
        result = talib.ADX(self.high, self.low, self.close, n)
        if array:
            return result
        return result[-1]
    
    #----------------------------------------------------------------------
    def boll(self, n, dev, array=False):
        """布林通道"""
        mid = self.sma(n, array)
        std = self.std(n, array)
        
        up = mid + std * dev
        down = mid - std * dev
        
        return up, down    

    #----------------------------------------------------------------------
    def new_boll(self, n, dev, array=False):
        """布林通道"""
        mid = self.sma(n, array)
        std = self.std(n, array)
        
        up = mid + std * dev
        b1 = 4 * std / mid
        b2 = (self.close[-1] - mid + 2 * std) / (4 * std)
        down = mid - std * dev
        
        return up, down, b1, b2   
    
    def bollingerb2(self, n, array=False):
        return self.new_boll(n, 2)[-1]

    def bollingerb(self, n, array=False):
        return self.new_boll(n, 2)[-2]

    def coppock_curve(self, n, array=False):
        df = pd.DataFrame()
        df['Close'] = pd.Series(self.close)
        M = df['Close'].diff(int(n * 11 / 10) - 1)
        N = df['Close'].shift(int(n * 11 / 10) - 1)
        ROC1 = M / N
        M = df['Close'].diff(int(n * 14 / 10) - 1)
        N = df['Close'].shift(int(n * 14 / 10) - 1)
        ROC2 = M / N
        return (ROC1 + ROC2).ewm(span=n, min_periods=n).mean()[-1]

    def aroonosc(self, n, array=False):
        high = self.high
        low = self.low
        real = talib.AROONOSC(high, low, timeperiod=n)
        if array:
            return real
        return real[-1]

    def bop(self, n, array=False):
        high = self.high
        low = self.low
        open_ = self.open
        close = self.close
        real = talib.BOP(open_, high, low, close)
        if array:
            return real
        return real[-1]

    def cmo(self, n, array=False):
        close = self.close
        real = talib.CMO(close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def dx(self, n, array=False):
        high = self.high
        low = self.low
        open_ = self.open
        close = self.close
        real = talib.DX(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def minusdi(self, n, array=False):
        high = self.high
        low = self.low
        close = self.close
        real = talib.MINUS_DI(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def minusdm(self, n, array=False):
        high = self.high
        low = self.low
        real = talib.MINUS_DM(high, low, timeperiod=n)
        if array:
            return real
        return real[-1]

    def mom(self, n, array=False):
        close = self.close
        real = talib.MOM(close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def plusdi(self, n, array=False):
        high = self.high
        low = self.low
        close = self.close
        real = talib.PLUS_DI(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def plusdm(self, n, array=False):
        high = self.high
        low = self.low
        real = talib.PLUS_DM(high, low, timeperiod=n)
        if array:
            return real
        return real[-1]

    def rocp(self, n, array=False):
        real = talib.ROCP(self.close, timeperiod=n)
        return real[-1]

    def rocr(self, n, array=False):
        real = talib.ROCR(self.close, timeperiod=n)
        return real[-1]

    def rocr100(self, n, array=False):
        real = talib.ROCR100(self.close, timeperiod=n)
        return real[-1]

    def natr(self, n, array=False):
        real = talib.NATR(self.close, timeperiod=n)
        return real[-1]

    def trix(self, n, array=False):
        #print("trix", n, self.close)
        trix = talib.TRIX(self.close, timeperiod=n)
        return trix[-1]

    def roc(self, n, array=False):
        roc = talib.ROC(self.close, timeperiod=n)
        if array:
            return roc
        return roc[-1]

    def willr(self, n, array=False):
        high = self.high
        low = self.low
        close = self.close
        real = talib.WILLR(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def adx(self, n, array=False):
        high = self.high
        low = self.low
        close = self.close
        real = talib.ADX(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def macd(self, n, array=False):
        close = self.close
        real, _, _ = talib.MACDFIX(close, signalperiod=n)
        if array:
            return real
        return real[-1]

    def macdhist(self, n, array=False):
        close = self.close
        _, _, real = talib.MACDFIX(close, signalperiod=n)
        if array:
            return real
        return real[-1]

    def macdsignal(self, n, array=False):
        close = self.close
        _, real, _ = talib.MACDFIX(close, signalperiod=n)
        if array:
            return real
        return real[-1]

    def adxr(self, n, array=False):
        high = self.high
        low = self.low
        close = self.close
        real = talib.ADXR(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def mfi(self, n, array=False):
        """Calculate Money Flow Index and Ratio for given data.
        """
        high = self.high
        low = self.low
        volume = self.volume
        close = self.close
        real = talib.MFI(high, low, close, volume, timeperiod=n)
        if array:
            return real
        return real[-1]

    def cci(self, n, array=False):
        """Calculate Commodity Channel Index for given data.
        """
        high = self.high
        low = self.low
        volume = self.volume
        close = self.close
        real = talib.CCI(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def natr(self, n, array=False):
        high = self.high
        low = self.low
        close = self.close
        real = talib.NATR(high, low, close, timeperiod=n)
        if array:
            return real
        return real[-1]

    def ad(self, n, array=False):
        high = self.high
        low = self.low
        volume = self.volume
        close = self.close
        real = talib.AD(high, low, close, volume)
        if array:
            return real
        return real[-1]

    def obv(self, n, array=False):
        volume = self.volume
        close = self.close
        real = talib.OBV(close, volume)
        if array:
            return real
        return real[-1]

    def stochastic_oscillator(self, n, array=False):
        """Calculate stochastic oscillator %D for given data.
        :param df: pandas.DataFrame
        :param n: 
        :return: pandas.DataFrame
        """
        df = pd.DataFrame()
        df['Close'] = pd.Series(self.close)
        df['Low'] = pd.Series(self.low)
        df['High'] = pd.Series(self.high)
        SOk = pd.Series((df['Close'] - df['Low']) / (df['High'] - df['Low']),
                         name='SOk')
        SOd = pd.Series(SOk.ewm(span=n, min_periods=n).mean(), name='SOd')
        df = df.join(SOd)
        df = df.join(SOk)
        return df['SOk'].values[-1], df['SOd'].values[-1]
    
    #----------------------------------------------------------------------
    def keltner(self, n, dev, array=False):
        """肯特纳通道"""
        mid = self.sma(n, array)
        atr = self.atr(n, array)
        
        up = mid + atr * dev
        down = mid - atr * dev
        
        return up, down
    
    #----------------------------------------------------------------------
    def donchian(self, n, array=False):
        """唐奇安通道"""
        up = talib.MAX(self.high, n)
        down = talib.MIN(self.low, n)
        
        if array:
            return up, down
        return up[-1], down[-1]
    

########################################################################
class CtaSignal(object):
    """
    CTA策略信号，负责纯粹的信号生成（目标仓位），不参与具体交易管理
    """

    #----------------------------------------------------------------------
    def __init__(self):
        """Constructor"""
        self.signalPos = 0      # 信号仓位
    
    #----------------------------------------------------------------------
    def onBar(self, bar):
        """K线推送"""
        pass
    
    #----------------------------------------------------------------------
    def onTick(self, tick):
        """Tick推送"""
        pass
        
    #----------------------------------------------------------------------
    def setSignalPos(self, pos):
        """设置信号仓位"""
        self.signalPos = pos
        
    #----------------------------------------------------------------------
    def getSignalPos(self):
        """获取信号仓位"""
        return self.signalPos
        
        
        
        
    
    
