import asyncio
import os
from decimal import Decimal
# from dotenv import load_dotenv

# Nautilus Trader imports
from nautilus_trader.adapters.bybit.common.enums import BybitProductType
from nautilus_trader.adapters.bybit.config import BybitDataClientConfig, BybitExecClientConfig
from nautilus_trader.adapters.bybit.factories import BybitLiveDataClientFactory, BybitLiveExecClientFactory
from nautilus_trader.config import InstrumentProviderConfig, LoggingConfig
from nautilus_trader.live.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId, TraderId
from nautilus_trader.config import LiveExecEngineConfig

# Your custom strategy/model imports
from strategy import MarketMaker, MarketMakerConfig

# Load credentials from .env (if used)
# load_dotenv()

async def main():
    """
    Run the strategy connected only to Bybit for data and execution.
    """
    # *** THIS IS A TEST STRATEGY WITH NO ALPHA ADVANTAGE WHATSOEVER. ***
    # *** IT IS NOT INTENDED TO BE USED TO TRADE LIVE WITH REAL MONEY. ***

    # SPOT/LINEAR
    product_type = BybitProductType.LINEAR
    symbol = f"BTCUSDT-{product_type.value.upper()}"

    # Configure the trading node
    config_node = TradingNodeConfig(
        trader_id=TraderId("TESTER-001"),
        logging=LoggingConfig(log_level="INFO", use_pyo3=True),
        exec_engine=LiveExecEngineConfig(
            reconciliation=True,
            reconciliation_lookback_mins=1440,
        ),
        data_clients={
            "BYBIT": BybitDataClientConfig(
                api_key='',  # 'BYBIT_API_KEY' env var
                api_secret='',  # 'BYBIT_API_SECRET' env var
                base_url_http=None,  # Override with custom endpoint
                instrument_provider=InstrumentProviderConfig(load_all=True),
                product_types=[product_type],
                testnet=True,  # If client uses the testnet
            ),
        },
        exec_clients={
            "BYBIT": BybitExecClientConfig(
                api_key='',  # 'BYBIT_API_KEY' env var
                api_secret='',  # 'BYBIT_API_SECRET' env var
                base_url_http=None,  # Override with custom endpoint
                # base_url_ws_private=None,  # Override with custom endpoint
                instrument_provider=InstrumentProviderConfig(load_all=True),
                product_types=[product_type],
                testnet=True,  # If client uses the testnet
                max_retries=3,
            ),
        },
        timeout_connection=30.0,
        timeout_reconciliation=10.0,
        timeout_portfolio=10.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )


    # Create the trading node
    node = TradingNode(config=config_node)

    # Configure the strategy
    strat_config = MarketMakerConfig(
        instrument_id=InstrumentId.from_str(f"{symbol}.BYBIT"),
        trade_size=Decimal("0.01"),
        spread_pct=Decimal("0.001"),  # 1% total spread
        inventory_threshold=Decimal("5.0"),
        quote_interval_seconds=5,
    )
    
    # Instantiate and add the strategy
    strategy = MarketMaker(config=strat_config)
    node.trader.add_strategy(strategy)

    # Register Bybit client factories
    node.add_data_client_factory("BYBIT", BybitLiveDataClientFactory)
    node.add_exec_client_factory("BYBIT", BybitLiveExecClientFactory)


    # Build and run the node
    node.build()
    try:
        await node.run_async()
    finally:
        await node.stop_async()
        await asyncio.sleep(1)
        node.dispose()

if __name__ == "__main__":
    asyncio.run(main())

