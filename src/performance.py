#!/usr/bin/python
# -*- coding: utf-8 -*-

# performance.py


import numpy as np
import pandas as pd


def sharpe_ratio(retornos, periodos,rf=0.04):#
    r=rf/periodos
    return ((np.mean(retornos)-r) / np.std(retornos))*np.sqrt(periodos)
   
    
def max_drawdowns(pnl):
    """
    Calcula la mayor distancia entre pico y valle de la curva de P&G
    así como la duración del Drawdown. Requiere que la serie de retorno 
    esté expresada como una serie de tiempo de pandas,

    Parametros:
    pnl - Serie de tiempo de Pandas que contiene retornos porcentuales periódicos
    .

    Retorna:
    Max drawdown, DD duration
    """

    # Calcula la curva de retorno acumulado 
    # establece la marca de agua (hwm)
    hwm = [0]

    # Crea la serie de MDD y MDDD
    idx = pnl.index
    drawdown = pd.Series(index = idx)
    duration = pd.Series(index = idx)

    # Ciclo sobre el rango del índice
    for t in range(1, len(idx)):
        hwm.append(max(hwm[t-1], pnl[t]))
        drawdown[t]= (hwm[t]-pnl[t])
        duration[t]= (0 if drawdown[t] == 0 else duration[t-1]+1)
    return drawdown, drawdown.max(), duration.max()

    
def kelly(retornos,frecuencia=365):
    rf=(1.08**(1/(frecuencia)))-1
    if np.std(retornos)==0:
        STNR=0
    else:
        STNR=((np.mean(retornos)-rf)/np.std(retornos)**2)
    return STNR
        
        