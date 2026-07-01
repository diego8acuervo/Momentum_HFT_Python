FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ gfortran libopenblas-dev git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY src/ src/
COPY config/ config/

RUN mkdir -p outputs src/outputs notebooks

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

ENTRYPOINT ["python", "src/AQM_Momentum_Live.py"]
CMD ["--tokens", "AAVE,AIXBT,AVAX,BCH,BNB,BTC,COMP,DOGE,DOT,DYDX,EIGEN,ENA,ETH,ETHFI,FORM,INJ,JUP,LTC,NEAR,PNUT,RAY,SOL,SUI,TRX,UNI,WIF", \
     "--capital", "100000", \
     "--variant", "turtle", \
     "--short-window", "5", \
     "--long-window", "30", \
     "--max-weight", "0.10", \
     "--stop-loss", "-0.15", \
     "--rebalance-utc", "0", \
     "--min-rebalance", "0.005", \
     "--batch-n", "3", \
     "--batch-interval", "600", \
     "--limit-offset-bps", "2", \
     "--exchange", "binance", \
     "--account", "binance_momentum", \
     "--state-file", "live_state_momentum.json", \
     "--interval", "1d", \
     "--gcp", \
     "--gcp-project", "aqm-trading-prod", \
     "--gcp-dataset", "trading"]
