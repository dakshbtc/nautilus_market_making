"""
Simple Market Making Strategy with Linear Inventory Skewing
=============================================================

A beginner-friendly market maker that automatically manages inventory risk
using a simple linear skewing formula.

Author: [Your Name]
YouTube: [Your Channel]
"""

from decimal import Decimal
from datetime import timedelta

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.trading.strategy import Strategy


# ==============================================================================
# CONFIGURATION
# ==============================================================================

class MarketMakerConfig(StrategyConfig, frozen=True):
    """
    Configuration for the MarketMaker strategy.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument to trade (e.g., "BTC-USD.COINBASE")
    trade_size : Decimal
        Size of each order (e.g., Decimal("0.1") for 0.1 BTC)
    spread_pct : Decimal, default Decimal("0.01")
        Total bid-ask spread as decimal (0.01 = 1% total, 0.5% each side)
    inventory_threshold : Decimal, default Decimal("5.0")
        Maximum position size before full skewing (e.g., 5.0 BTC)
        At this size, skewing reaches its maximum (50% tightening)
    quote_interval_seconds : int, default 30
        How often to refresh quotes in seconds
    close_positions_on_stop : bool, default True
        Whether to close all positions when strategy stops

    Examples
    --------
    >>> config = MarketMakerConfig(
    ...     instrument_id=InstrumentId.from_str("BTC-USD.COINBASE"),
    ...     trade_size=Decimal("0.1"),
    ...     spread_pct=Decimal("0.01"),  # 1% total spread
    ...     inventory_threshold=Decimal("5.0"),  # Max 5 BTC position
    ... )
    """

    instrument_id: InstrumentId
    trade_size: Decimal
    spread_pct: Decimal = Decimal("0.01")
    inventory_threshold: Decimal = Decimal("5.0")
    quote_interval_seconds: int = 30
    close_positions_on_stop: bool = True


# ==============================================================================
# STRATEGY
# ==============================================================================

class MarketMaker(Strategy):
    """
    A time-based market making strategy with simple linear inventory skewing.

    Overview
    --------
    This strategy:
    1. Places buy and sell orders around the market mid price
    2. Refreshes quotes on a timer (not on every market update)
    3. Tightens one side when inventory builds up (risk management)
    4. Uses a simple linear formula for easy-to-understand skewing

    Skewing Logic (SIMPLIFIED)
    ---------------------------
    skew = (current_position / threshold) * 50%
    
    - Position at 0% → No skewing (balanced spreads)
    - Position at 50% → 25% skewing (gentle tightening)
    - Position at 100% → 50% skewing (maximum tightening)
    
    When LONG: Tightens the ASK (sell) side to encourage selling
    When SHORT: Tightens the BID (buy) side to encourage buying

    Example
    -------
    Normal (no position):
      Buy: $99.50 (-0.5%) | Mid: $100 | Sell: $100.50 (+0.5%)
    
    Long 2.5 BTC (50% of inventory_threshold = 5):
      Buy: $99.50 (-0.5%) | Mid: $100 | Sell: $100.38 (+0.38%)
                                                   ↑ Tightened by 25%
    """

    def __init__(self, config: MarketMakerConfig) -> None:
        super().__init__(config)

        # Store configuration
        self.instrument_id = config.instrument_id
        self.trade_size = config.trade_size
        self.spread_pct = config.spread_pct
        self.inventory_threshold = config.inventory_threshold
        self.quote_interval_seconds = config.quote_interval_seconds
        self.close_positions_on_stop = config.close_positions_on_stop

        # Initialize state
        self.instrument: Instrument | None = None
        self._book: OrderBook | None = None
        self._current_mid: Decimal | None = None

    # ==========================================================================
    # LIFECYCLE METHODS
    # ==========================================================================

    def on_start(self) -> None:
        """Initialize strategy when it starts."""
        # Get instrument details
        self.instrument = self.cache.instrument(self.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument {self.instrument_id}")
            self.stop()
            return

        # Create order book to track market prices
        self._book = OrderBook(
            instrument_id=self.instrument.id,
            book_type=BookType.L2_MBP,
        )

        # Subscribe to order book updates
        self.subscribe_order_book_deltas(
            instrument_id=self.instrument.id,
            book_type=BookType.L2_MBP,
            depth=50,
        )

        # Set up timer for periodic quote refresh
        self.clock.set_timer(
            name="quote_timer",
            interval=timedelta(seconds=self.quote_interval_seconds),
            callback=self._on_quote_timer
        )

        self.log.info(
            f"Market Maker started | "
            f"Spread: {self.spread_pct * 100:.2f}% | "
            f"Max Position: {self.inventory_threshold} | "
            f"Refresh: {self.quote_interval_seconds}s"
        )

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        """
        Process order book updates.
        
        Note: We only use this to track the mid price.
        We don't requote here - that happens on the timer.
        """
        if not self._book:
            return

        # Update our local order book
        self._book.apply_deltas(deltas)

        # Calculate current mid price
        bid = self._book.best_bid_price()
        ask = self._book.best_ask_price()
        
        self.log.info(f"Bid: {bid}, Ask: {ask}")
        
        if bid and ask:
            self._current_mid = Decimal((bid + ask) / 2)

    def on_stop(self) -> None:
        """Clean up when strategy stops."""
        self.clock.cancel_timer("quote_timer")
        self.cancel_all_orders(self.instrument_id)

        if self.close_positions_on_stop:
            self.close_all_positions(self.instrument_id)

        self.log.info("Market Maker stopped")

    # ==========================================================================
    # TIMER CALLBACK
    # ==========================================================================

    def _on_quote_timer(self, event) -> None:
        """
        Called every X seconds by the timer.
        This is where we refresh our quotes.
        """
        if not self._current_mid or not self.instrument:
            self.log.warning("Cannot quote - no mid price available")
            return

        self.log.info("Refreshing quotes...")

        # Cancel old orders
        self.cancel_all_orders(self.instrument_id)

        # Place new orders
        self._place_orders()

    # ==========================================================================
    # CORE LOGIC
    # ==========================================================================

    def _place_orders(self) -> None:
        """
        Place buy and sell orders with simple linear skewing.

        Algorithm:
        1. Get current position
        2. Calculate skew percentage (0% to 50%)
        3. Apply skew to tighten one side
        4. Place orders
        """
        self.log.info("Placing orders...")

        # Step 1: Get current position
        position = self._get_position()

        # Step 2: Calculate skew amount (as fraction of half-spread)
        skew_fraction = self._calculate_skew(position)

        # Step 3: Start with symmetric spreads
        half_spread = self.spread_pct / Decimal(2)
        bid_spread = half_spread
        ask_spread = half_spread

        # Apply skew by reducing one side
        if position > 0:  # Long position → tighten ask to sell
            reduction = half_spread * skew_fraction
            ask_spread -= reduction
            self.log.info(f"LONG {position} → Tightening ASK by {skew_fraction*100:.1f}%")
        elif position < 0:  # Short position → tighten bid to buy
            reduction = half_spread * skew_fraction
            bid_spread -= reduction
            self.log.info(f"SHORT {position} → Tightening BID by {skew_fraction*100:.1f}%")

        # Safety check: spreads can't be negative
        bid_spread = max(bid_spread, Decimal("0"))
        ask_spread = max(ask_spread, Decimal("0"))

        self.log.info(f"Final spreads → Bid: {bid_spread*100:.3f}%, Ask: {ask_spread*100:.3f}%")

        # Step 4: Calculate final prices
        buy_price = self._current_mid * (Decimal(1) - bid_spread)
        sell_price = self._current_mid * (Decimal(1) + ask_spread)

        # Step 5: Submit orders
        self._submit_buy(buy_price)
        self._submit_sell(sell_price)

    # ==========================================================================
    # SKEW CALCULATION (SIMPLIFIED!)
    # ==========================================================================

    def _calculate_skew(self, position: Decimal) -> Decimal:
        """
        Calculate how much to tighten spreads based on position.
        
        Simple linear formula:
        - At 0% of max → 0% tightening
        - At 50% of max → 25% tightening  
        - At 100% of max → 50% tightening (maximum)
        
        Parameters
        ----------
        position : Decimal
            Current signed position (positive=long, negative=short)
            
        Returns
        -------
        Decimal
            Fraction to tighten one side (0.0 to 0.5)
            
        Examples
        --------
        If position is 2.5 BTC and inventory_threshold is 5 BTC:
        - ratio = 2.5 / 5 = 0.5 (50% of max)
        - skew = 0.5 * 0.5 = 0.25 (tighten by 25%)
        
        If position is 5.0 BTC and inventory_threshold is 5 BTC:
        - ratio = 5.0 / 5 = 1.0 (100% of max)
        - skew = 1.0 * 0.5 = 0.5 (tighten by 50% - maximum!)
        """
        if self.inventory_threshold == 0:
            return Decimal(0)
        
        # How close are we to our max position?
        position_ratio = abs(position) / self.inventory_threshold
        
        # Maximum tightening is 50% (half the spread goes to zero)
        MAX_SKEW = Decimal("0.5")
        
        # Linear scaling: the closer to max position, the more we skew
        skew_amount = position_ratio * MAX_SKEW
        
        # Cap at maximum (in case position exceeds our limit)
        return min(skew_amount, MAX_SKEW)

    # ==========================================================================
    # HELPER METHODS
    # ==========================================================================

    def _get_position(self) -> Decimal:
        """
        Get current position from cache.

        Returns
        -------
        Decimal
            Signed position (positive=long, negative=short, 0=flat)
        """
        position = self.cache.positions_open(instrument_id=self.instrument_id)
        if not position:
            self.log.info("No position found")
            return Decimal(0)

        self.log.info(f"Positionnnn: {position[0]}")
        qty = position[0].signed_qty.as_decimal()
        self.log.info(f"Position: {qty}")
        return qty

    def _submit_buy(self, price: Decimal) -> None:
        """Submit a buy limit order."""
        self.log.info(f"Submitting buy order at {price}")
        
        if not self.instrument:
            return

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            price=Price(price, precision=self.instrument.price_precision),
            quantity=self.instrument.make_qty(self.trade_size),
        )
        self.submit_order(order)

    def _submit_sell(self, price: Decimal) -> None:
        """Submit a sell limit order."""
        if not self.instrument:
            return

        order = self.order_factory.limit(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            price=Price(price, precision=self.instrument.price_precision),
            quantity=self.instrument.make_qty(self.trade_size),
        )
        self.submit_order(order)