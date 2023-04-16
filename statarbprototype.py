#region imports
from AlgorithmImports import *
#endregion
import numpy as np
import pandas as pd
import math
import time

class StatArb1(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2012, 1,4)
        self.SetEndDate(2012,7,15)
        self.SetCash(1000000)
        
        self.SetWarmup(10)
        
        self.UniverseSettings.Resolution = Resolution.Minute
        self.UniverseSettings.MinimumTimeInUniverse = timedelta(hours=12)
        self.AddUniverse(self.LiquidWithFundamentalsFilter)

        self.spy = self.AddEquity("SPY",Resolution.Minute)
        
        self.Schedule.On(self.DateRules.EveryDay("SPY"),
                self.TimeRules.BeforeMarketClose("SPY", 1),        
                self.Liquidate)
                 
        self.Schedule.On(self.DateRules.EveryDay("SPY"),
                self.TimeRules.AfterMarketOpen("SPY", 60),
                #self.TimeRules.Every(timedelta(minutes=1)),        
                self.selectiveLiquidate)
                
        self.Schedule.On(self.DateRules.EveryDay("SPY"),
                self.TimeRules.BeforeMarketClose("SPY", 10),        
                self.cancelLingeringOrders)
        
        self.Schedule.On(self.DateRules.EveryDay("SPY"),
                self.TimeRules.AfterMarketOpen("SPY", 5),        
                self.RefactorPortfolio)
                 
        self.Schedule.On(self.DateRules.EveryDay("SPY"),
                self.TimeRules.BeforeMarketClose("SPY", 1),        
                lambda: self.Plot("Equity", 'Line', self.Portfolio.TotalPortfolioValue))
        
        self.currentHoldings = None
        
                 
        self.lookback = 20
        self.activeStocks = []
        self.maxWeight = .1
        self.orderTickets = {}
        self.holdingCutoff = 0.85
        self.changeTolerance = 0.007
        
        self.flag = 0

    
    def LiquidWithFundamentalsFilter(self, coarse):
        sortedByDollarVolume = sorted(coarse, key=lambda x: x.DollarVolume, reverse=True)
        filtered = [ x.Symbol for x in sortedByDollarVolume 
                      if x.Price > 10 and x.DollarVolume > 10000000 and 
                       x.HasFundamentalData]

        return filtered[:100]
        
        
    def OnSecuritiesChanged(self, changes):
        for s in changes.RemovedSecurities:
            if s.Symbol in self.activeStocks:
                self.activeStocks.remove(s.Symbol)
        for s in changes.AddedSecurities:
            if str(s.Symbol) == "SPY": continue
            self.activeStocks.append(s.Symbol)


    def RefactorPortfolio(self):
        for security in self.activeStocks:
            #self.Securities[security].FeeModel = ConstantFeeModel(0)
            self.Securities[security].SetSlippageModel(ConstantSlippageModel(0.001))
        
        tickers = [s.ToString() for s in self.activeStocks if s.ToString() != "SPY"]
        data = self.History(tickers,self.lookback,Resolution.Daily)
        dates = data.index.get_level_values("time")
        
        #self.Debug("###"+str(self.Time)+"###"+str(dates[-1])+", "+str(len(tickers))+", "+str(len(self.ActiveSecurities)))
        
        tickerIndustries = {}
        for ticker in tickers:
            tickerIndustries[ticker] = self.ActiveSecurities[ticker].Fundamentals.AssetClassification.MorningstarIndustryCode
        
        alpha = {}
        self.dailyOpens = {}
        
        for ticker in tickers:
            try:
                volumedata = data.loc[(ticker,slice(None)),"volume"]
                lenvolume = len(volumedata)
                sumvolume = sum([x for x in volumedata])
                volume = data.loc[(ticker,dates[-1]),"volume"]
                adv20 = sumvolume/lenvolume
                
                delay1close = data.loc[(ticker,dates[-2]),"close"]
                todaysOpen = data.loc[(ticker,dates[-1]),"open"]
                
                value = -np.log(todaysOpen/delay1close)*volume/adv20
            except:
                self.Debug("Alpha Calculation Threw Error")
                value = np.NAN
    
            alpha[ticker] = value
        
        holdingLevels = {}
        
        alpha = self.Neutralize(alpha, tickerIndustries)
        
        for ticker in alpha:
            if np.isnan(alpha[ticker]):
                alpha[ticker] = 0
            if abs(alpha[ticker]) > self.maxWeight:
                alpha[ticker] = np.sign(alpha[ticker])*self.maxWeight
            
            holdingLevel = alpha[ticker]*self.holdingCutoff
            holdingLevels[ticker] = holdingLevel
            
            equity = self.Portfolio.TotalPortfolioValue
            p = self.CurrentSlice.Bars[ticker].Close
            q = math.floor(equity*holdingLevel/p)
            
            self.LimitOrder(ticker,q,p)
            
        self.currentHoldings = holdingLevels
        self.refactorTime = self.CurrentSlice.UtcTime
        
            
    def Neutralize(self, alpha, groupClassifications):
        groupValues = {}
        groupAvgs = {}
        groupTotalMagnitude = {}
        
        for group in set(groupClassifications.values()):
            groupValues[group] = []
            
        for ticker in alpha.keys():
            groupValues[groupClassifications[ticker]].append(alpha[ticker])
            
        for group in set(groupClassifications.values()):
            groupAvgs[group] = np.nanmean(np.array(groupValues[group]))
            groupTotalMagnitude[group] = np.nansum(np.array([abs(x) for x in groupValues[group]]))
            
        for ticker in alpha.keys():
            group = groupClassifications[ticker]
            alpha[ticker] = (alpha[ticker]-groupAvgs[group])/(groupTotalMagnitude[group])
            
        return alpha
        
    def selectiveLiquidate(self):
        positions = self.Transactions.GetOrders(lambda x: x.Time == self.refactorTime)
        
        if self.flag == 0:
            self.ss = [x.Symbol.ToString() for x in positions]
            self.flag += 1
        
        for order in positions:
                ticker = order.Symbol.ToString()
                q = self.Portfolio[ticker].Quantity
                sign = np.sign(q)
                multiplier = 1+self.changeTolerance if sign > 0 else 1-self.changeTolerance
                
                if q != 0:
                    p = (self.Portfolio[ticker].HoldingsCost/q)*multiplier
                    self.orderTickets[ticker] = self.LimitOrder(ticker, -q, p)
                                
        
    def cancelLingeringOrders(self):
        openOrders = self.Transactions.GetOpenOrders()
        for x in openOrders:
            self.Transactions.CancelOrder(x.Id)
        
    def OnEndOfAlgorithm(self):
        self.Debug(str(self.ss))

    def OnData(self, data):
        pass
        #self.Plot("Margin", 'Line', self.Portfolio.TotalMarginUsed)    
