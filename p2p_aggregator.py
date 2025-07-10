#!/usr/bin/env python3
"""
P2P Bitcoin Order Aggregator

This script connects to Nostr relays to aggregate P2P Bitcoin exchange orders
with advanced filtering, logging, and reporting capabilities.

Usage:
    python3 p2p_aggregator.py -t 1 -c USD,EUR,BTC
    python3 p2p_aggregator.py --scan-time 5 --currencies btc,usd,ars
    python3 p2p_aggregator.py -h
"""

import requests
import time
import asyncio
import argparse
import sys
import os
import copy
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
from nostr_sdk import Client, Filter, Kind, SingleLetterTag

# --- Configuration ---
NOSTR_RELAYS = [
    "wss://nostr.satstralia.com",
    "wss://relay.damus.io", 
    "wss://relay.snort.social",
    "wss://nos.lol",
    "wss://relay.mostro.network",
]

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd,eur,gbp,jpy,cad,aud,chf,cny,krw,inr,brl,rub,mxn,zar,sgd,hkd,nok,sek,dkk,pln,czk,huf,try,ils,thb,ars,clp,cop,pen,ves"
YADIO_URL = "https://api.yadio.io/exrates/BTC"

# Crypto aliases for payment method detection
CRYPTO_ALIASES = {
    'BTC': ['bitcoin', 'btc'],
    'USDT': ['tether', 'usdt'], 
    'USDC': ['usdc'],
    'ETH': ['ethereum', 'ether', 'eth'],
    'XMR': ['monero', 'xmr'],
    'LTC': ['litecoin', 'ltc'],
    'BCH': ['bch', 'bitcoin cash']
}

# Close fiat equivalents for crypto (USD stablecoins)
FIAT_CRYPTO_EQUIVALENTS = {
    'USD': ['USDT', 'USDC'],
}

# Supported currencies (fiat + crypto)
SUPPORTED_CURRENCIES = {
    # Fiat currencies
    'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF', 'CNY', 'KRW', 'INR',
    'BRL', 'RUB', 'MXN', 'ZAR', 'SGD', 'HKD', 'NOK', 'SEK', 'DKK', 'PLN', 
    'CZK', 'HUF', 'TRY', 'ILS', 'THB', 'ARS', 'CLP', 'COP', 'PEN', 'VES',
    'CUP',  # Cuban Peso (observed in data)
    # Crypto currencies  
    'BTC', 'XMR', 'USDT', 'USDC', 'ETH', 'LTC', 'BCH'
}

# Currency normalization mapping
CURRENCY_MAP = {
    'USDT': 'USD', 'USDC': 'USD', 'US$': 'USD', 'BUSD': 'USD',
    'DOLLAR': 'USD', 'DOLLARS': 'USD', '€': 'EUR', 'EURO': 'EUR',
    'EUROS': 'EUR', '£': 'GBP', 'POUND': 'GBP', 'POUNDS': 'GBP',
    'STERLING': 'GBP', '¥': 'JPY', 'YEN': 'JPY',
}

class OrderData:
    """Data class for P2P order information"""
    def __init__(self, event_data: Dict):
        self.id = event_data.get('id', '')
        self.pubkey = event_data.get('pubkey', '')
        self.source = event_data.get('source', '')
        self.order_type = event_data.get('type', '')
        self.amount = event_data.get('amount', '')
        self.currency = event_data.get('currency', '')
        self.link = event_data.get('link', '')
        self.created_at = event_data.get('created_at', 0)
        self.premium_percent = event_data.get('premium_percent')
        self.bond_percent = event_data.get('bond_percent')
        self.payment_methods = event_data.get('payment_methods', '')
        self.price_btc = event_data.get('price_btc', '')
        
    def get_premium_float(self) -> float:
        """Convert premium to float for sorting"""
        if self.premium_percent is None:
            return 0.0
        try:
            return float(self.premium_percent)
        except (ValueError, TypeError):
            return 0.0

def get_utc_timestamp() -> str:
    """Get current UTC timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def get_filename_timestamp() -> str:
    """Get UTC timestamp formatted for filename"""
    return datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')

def parse_time_input(time_str):
    """Parse time input with support for seconds (s) and minutes (m) suffixes"""
    if isinstance(time_str, int):
        # If it's already an integer, assume seconds
        return time_str
    
    time_str = str(time_str).lower().strip()
    
    if time_str.endswith('s'):
        # Seconds format: "30s"
        try:
            seconds = int(time_str[:-1])
            return seconds
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid seconds format: {time_str}")
    elif time_str.endswith('m'):
        # Minutes format: "2m"
        try:
            minutes = int(time_str[:-1])
            return minutes * 60  # Convert to seconds
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid minutes format: {time_str}")
    else:
        # Plain integer, assume seconds
        try:
            seconds = int(time_str)
            return seconds
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid time format: {time_str}")

def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='P2P Bitcoin Order Aggregator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Supported Currencies:
  Fiat: {', '.join(sorted([c for c in SUPPORTED_CURRENCIES if c not in ['BTC', 'XMR', 'USDT', 'USDC', 'ETH', 'LTC', 'BCH']]))}
  Crypto: BTC, XMR, USDT, USDC, ETH, LTC, BCH

Examples:
  python3 p2p_aggregator.py -t 30 -c USD,EUR,BTC        # 30 seconds
  python3 p2p_aggregator.py -t 45s --currencies btc,usd # 45 seconds
  python3 p2p_aggregator.py -t 2m                       # 2 minutes
  python3 p2p_aggregator.py -t 30                       # 30 seconds (default)
        """
    )
    
    parser.add_argument(
        '-t', '--scan-time',
        type=parse_time_input,
        default=30,
        help='Scan duration: plain number=seconds, ##s=seconds, ##m=minutes (10s-5m, default: 30s)'
    )
    
    parser.add_argument(
        '-c', '--currencies',
        type=str,
        default='',
        help='Comma-separated currency list (default: all supported currencies)'
    )
    
    args = parser.parse_args()
    
    # Validate scan time (now in seconds)
    if not 10 <= args.scan_time <= 300:  # 10 seconds to 5 minutes
        print("Error: Scan time must be between 10 seconds and 5 minutes")
        sys.exit(1)
    
    # Parse and validate currencies
    if args.currencies:
        currencies = [c.strip().upper() for c in args.currencies.split(',')]
        invalid_currencies = [c for c in currencies if c not in SUPPORTED_CURRENCIES]
        if invalid_currencies:
            print(f"Error: Unsupported currencies: {', '.join(invalid_currencies)}")
            print(f"Supported: {', '.join(sorted(SUPPORTED_CURRENCIES))}")
            sys.exit(1)
        args.currencies = currencies
    else:
        args.currencies = list(SUPPORTED_CURRENCIES)
    
    # Validate that at least one crypto currency is included
    crypto_currencies = {'BTC', 'XMR', 'USDT', 'USDC', 'ETH', 'LTC', 'BCH'}
    if not any(currency in crypto_currencies for currency in args.currencies):
        print("Error: At least one crypto currency must be included (BTC, XMR, USDT, USDC, ETH, LTC, BCH)")
        sys.exit(1)
    
    return args

def update_exchange_rates() -> Dict[str, float]:
    """Fetch exchange rates from multiple APIs and return averaged rates"""
    print("Fetching exchange rates...")
    all_rates = {}
    
    # Fetch from CoinGecko
    try:
        response = requests.get(COINGECKO_URL, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if 'bitcoin' in data:
                rates = {k.upper(): v for k, v in data['bitcoin'].items()}
                for currency, rate in rates.items():
                    if currency not in all_rates:
                        all_rates[currency] = []
                    all_rates[currency].append(rate)
                print("✓ Fetched rates from CoinGecko")
    except Exception as e:
        print(f"⚠ Error fetching from CoinGecko: {e}")

    # Fetch from Yadio
    try:
        response = requests.get(YADIO_URL, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if 'BTC' in data:
                rates = {k.upper(): v for k, v in data['BTC'].items()}
                for currency, rate in rates.items():
                    if currency not in all_rates:
                        all_rates[currency] = []
                    all_rates[currency].append(rate)
                print("✓ Fetched rates from Yadio")
    except Exception as e:
        print(f"⚠ Error fetching from Yadio: {e}")
        
    # Calculate average rates
    average_rates = {
        currency: sum(values) / len(values)
        for currency, values in all_rates.items()
    }
    
    print(f"Exchange rates updated for {len(average_rates)} currencies")
    return average_rates

def calculate_btc_price(amount: float, currency_code: str, premium: Optional[str], 
                       exchange_rates: Dict[str, float]) -> Optional[str]:
    """Calculate BTC price in given currency with premium applied"""
    if not amount or not currency_code or currency_code not in exchange_rates:
        return None
    
    base_rate = exchange_rates[currency_code]
    final_rate = base_rate
    
    if premium:
        try:
            premium_percent = float(premium) / 100
            final_rate = base_rate * (1 + premium_percent)
        except (ValueError, TypeError):
            pass
        
    return f"{final_rate:,.0f} {currency_code}/BTC"

def extract_crypto_payment_methods(payment_methods_str: str, original_currency: str) -> List[str]:
    """Extract crypto currencies from payment methods, avoiding duplicates"""
    found_cryptos = []
    payment_lower = payment_methods_str.lower()
    
    for crypto, aliases in CRYPTO_ALIASES.items():
        if crypto == original_currency:
            continue  # Skip if same as original currency
        if any(alias in payment_lower for alias in aliases):
            found_cryptos.append(crypto)
    
    return found_cryptos

def should_keep_fiat_amounts(original_currency: str, crypto_currency: str) -> bool:
    """Determine if we should keep fiat amounts or convert to crypto"""
    if original_currency in FIAT_CRYPTO_EQUIVALENTS:
        return crypto_currency in FIAT_CRYPTO_EQUIVALENTS[original_currency]
    return False

def convert_to_crypto_amount(amount_str: str, original_currency: str, crypto_currency: str, 
                           exchange_rates: Dict[str, float]) -> str:
    """Convert fiat amount to crypto amount (placeholder implementation)"""
    # For now, just indicate it's a crypto amount
    # In a full implementation, you'd convert using exchange rates
    return f"{amount_str} (crypto equivalent)"

def generate_derived_orders(original_order: OrderData, crypto_payment_methods: List[str], 
                          exchange_rates: Dict[str, float]) -> List[OrderData]:
    """Generate additional orders for each crypto payment method"""
    derived_orders = []
    
    for crypto in crypto_payment_methods:
        derived_order = copy.deepcopy(original_order)
        derived_order.currency = crypto
        
        # Handle amount conversion logic
        if should_keep_fiat_amounts(original_order.currency, crypto):
            derived_order.amount = f"{original_order.amount} {original_order.currency} equivalent"
        else:
            # Convert to crypto amounts
            derived_order.amount = convert_to_crypto_amount(original_order.amount, 
                                                          original_order.currency, 
                                                          crypto, exchange_rates)
        
        # Update the price_btc calculation for the new currency
        derived_order.price_btc = f"Derived from {original_order.currency}/BTC"
        
        derived_orders.append(derived_order)
    
    return derived_orders

def process_event(event, exchange_rates: Dict[str, float], 
                 currency_filter: Set[str]) -> List[OrderData]:
    """Process a Nostr event and return list of OrderData (original + derived orders)"""
    try:
        tags = {tag.as_vec()[0]: tag.as_vec()[1:] for tag in event.tags().to_vec()}
        
        # Check for status - only process "pending" events
        status_tag = tags.get('s', [None])
        if not status_tag or status_tag[0] != 'pending':
            return []
        
        # Check for expiration
        if 'expiration' in tags and tags['expiration']:
            try:
                expiration_timestamp = int(tags['expiration'][0])
                if expiration_timestamp < time.time():
                    return []
            except (ValueError, IndexError):
                pass

        source = tags.get('y', ['-'])[0]
        
        # Special handling for robosats links
        if source == 'robosats' and 'source' in tags:
            link = tags['source'][0]
            coordinators = {
                'over the moon': 'moon',
                'bitcoinveneto': 'veneto', 
                'thebiglake': 'lake',
                'templeofsats': 'temple',
            }
            for coord, replacement in coordinators.items():
                link = link.replace(coord, replacement)
            tags['source'] = [link]

        currency_code = tags.get('f', [None])[0]
        if not currency_code:
            return []
            
        currency_code = currency_code.upper()
        currency_code = CURRENCY_MAP.get(currency_code, currency_code)

        amount_tag = tags.get('fa', [])
        formatted_amount = '-'
        raw_amount = None
        if len(amount_tag) == 1:
            try:
                raw_amount = float(amount_tag[0])
                formatted_amount = f"{raw_amount:,}"
            except ValueError:
                pass
        elif len(amount_tag) >= 2:
            try:
                min_amount = float(amount_tag[-2])
                max_amount = float(amount_tag[-1])
                formatted_amount = f"{min_amount:,} - {max_amount:,}"
                raw_amount = max_amount
            except ValueError:
                pass

        premium = tags.get('premium', [None])[0]
        payment_methods_list = tags.get('pm', [])

        # Create the original order
        event_data = {
            'id': event.id().to_hex(),
            'pubkey': event.author().to_hex(),
            'source': source,
            'type': tags.get('k', ['-'])[0],
            'amount': formatted_amount,
            'currency': currency_code,
            'link': tags.get('source', ['-'])[0],
            'created_at': event.created_at().as_secs(),
            'premium_percent': premium,
            'bond_percent': tags.get('bond', [None])[0],
            'payment_methods': ' '.join(payment_methods_list),
            'price_btc': calculate_btc_price(raw_amount, currency_code, premium, exchange_rates)
        }
        
        original_order = OrderData(event_data)
        
        # Extract crypto payment methods to generate derived orders
        payment_methods_str = ' '.join(payment_methods_list)
        crypto_payment_methods = extract_crypto_payment_methods(payment_methods_str, currency_code)
        
        # Generate all possible orders (original + derived)
        all_orders = [original_order]
        if crypto_payment_methods:
            derived_orders = generate_derived_orders(original_order, crypto_payment_methods, exchange_rates)
            all_orders.extend(derived_orders)
        
        # Filter orders based on currency filter
        filtered_orders = []
        for order in all_orders:
            if not currency_filter or order.currency in currency_filter:
                filtered_orders.append(order)
        
        return filtered_orders

    except Exception as e:
        print(f"⚠ Error processing event {event.id().to_hex()}: {e}")
        return []

def print_real_time_update(recent_orders: List[OrderData], total_orders: int, scan_start: datetime):
    """Print the last 5 orders found in real-time"""
    current_time = get_utc_timestamp()[:19].replace('T', ' ')
    elapsed = (datetime.now(timezone.utc) - scan_start).total_seconds()
    
    # Print header with stats
    print(f"\r[{current_time}] Found {total_orders} orders | Elapsed: {elapsed:.0f}s | Last 5 orders:")
    
    # Show last 5 orders (or fewer if less than 5 found)
    display_orders = recent_orders[-5:] if len(recent_orders) >= 5 else recent_orders
    
    for i in range(5):
        if i < len(display_orders):
            order = display_orders[i]
            premium_str = f"{order.premium_percent}%" if order.premium_percent else "0%"
            amount_str = str(order.amount)[:15] + "..." if len(str(order.amount)) > 15 else str(order.amount)
            
            # Get trade ID or URL (first 50 chars max)
            if order.link and order.link != '-':
                trade_id = order.link[:50] + "..." if len(order.link) > 50 else order.link
            else:
                trade_id = order.id[:50] + "..." if len(order.id) > 50 else order.id
            
            line = f"  {order.order_type.upper():<4} {order.currency}/BTC @ {premium_str:<6} | {order.source:<9} | {amount_str:<18} | {trade_id}"
            print(f"\r{line[:120]:<120}")
        else:
            # Print empty line to maintain 5-line display
            print(f"\r{'':<120}")
    
    # Move cursor back up to overwrite these lines next time
    print("\033[6A", end="")  # Move cursor up 6 lines (header + 5 order lines)

def generate_final_report(orders: List[OrderData], currency_filter: List[str], 
                         scan_duration: int) -> str:
    """Generate the final summary report"""
    if not orders:
        return "No orders found during scan period."
    
    report = []
    report.append("=" * 80)
    report.append(f"BEST P2P OPPORTUNITIES BY CURRENCY PAIR ({scan_duration} minute scan)")
    report.append("=" * 80)
    
    # Group orders by currency
    currency_orders = {}
    for order in orders:
        if order.currency not in currency_orders:
            currency_orders[order.currency] = {'buy': [], 'sell': []}
        currency_orders[order.currency][order.order_type].append(order)
    
    # Sort currencies by activity
    sorted_currencies = sorted(currency_orders.keys(), 
                              key=lambda c: len(currency_orders[c]['buy']) + len(currency_orders[c]['sell']), 
                              reverse=True)
    
    for currency in sorted_currencies:
        if currency == 'BTC':  # Skip BTC as base currency
            continue
            
        pair_orders = currency_orders[currency]
        buy_orders = sorted(pair_orders['buy'], key=lambda x: x.get_premium_float())[:3]
        sell_orders = sorted(pair_orders['sell'], key=lambda x: x.get_premium_float())[:3]
        
        if not buy_orders and not sell_orders:
            continue
            
        report.append(f"\n{currency}/BTC PAIR:")
        
        if buy_orders:
            report.append("  TOP 3 BUY ORDERS (best premiums):")
            for i, order in enumerate(buy_orders, 1):
                premium_str = f"{order.premium_percent}%" if order.premium_percent else "0%"
                report.append(f"    {i}. {premium_str} premium | {order.source} | {order.amount} {order.currency} | {order.payment_methods}")
                if order.link and order.link != '-':
                    report.append(f"       Link: {order.link}")
                else:
                    report.append(f"       Event ID: {order.id[:16]}... (Nostr)")
        
        if sell_orders:
            report.append("  TOP 3 SELL ORDERS (best premiums):")
            for i, order in enumerate(sell_orders, 1):
                premium_str = f"{order.premium_percent}%" if order.premium_percent else "0%"
                report.append(f"    {i}. {premium_str} premium | {order.source} | {order.amount} {order.currency} | {order.payment_methods}")
                if order.link and order.link != '-':
                    report.append(f"       Link: {order.link}")
                else:
                    report.append(f"       Event ID: {order.id[:16]}... (Nostr)")
    
    # Summary statistics
    buy_count = sum(len(orders_dict['buy']) for orders_dict in currency_orders.values())
    sell_count = sum(len(orders_dict['sell']) for orders_dict in currency_orders.values())
    sources = set(order.source for order in orders)
    
    report.append(f"\nSUMMARY: {len(orders)} total orders | {buy_count} BUY, {sell_count} SELL | {len(sources)} platforms")
    report.append(f"Sources: {', '.join(sorted(sources))}")
    
    return '\n'.join(report)

def create_csv_filename(currency_filter: List[str]) -> str:
    """Generate CSV filename based on currency filter and timestamp"""
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    if len(currency_filter) == len(SUPPORTED_CURRENCIES):
        currency_str = "ALL"
    else:
        currency_str = "-".join(sorted(currency_filter))
    return f"offers-{currency_str}-{timestamp}.csv"

def write_csv_file(orders: List[OrderData], filename: str, currency_filter: List[str], 
                   scan_duration: int, scan_start: datetime, scan_end: datetime):
    """Write all orders to CSV file"""
    try:
        import csv
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Write header row
            writer.writerow([
                'timestamp', 'source', 'type', 'currency', 'amount', 
                'premium', 'payment_methods', 'link', 'event_id'
            ])
            
            # Sort orders by timestamp
            sorted_orders = sorted(orders, key=lambda x: x.created_at)
            
            # Write orders
            for order in sorted_orders:
                timestamp = datetime.fromtimestamp(order.created_at, tz=timezone.utc).isoformat().replace('+00:00', 'Z')
                premium = order.premium_percent if order.premium_percent else "0"
                link = order.link if order.link and order.link != '-' else ""
                
                writer.writerow([
                    timestamp,
                    order.source,
                    order.order_type,
                    order.currency,
                    order.amount,
                    premium,
                    order.payment_methods,
                    link,
                    order.id
                ])
        
        print(f"✓ CSV written to: {filename}")
        
    except Exception as e:
        print(f"⚠ Error writing CSV file: {e}")

async def main():
    """Main function to run the P2P aggregator"""
    args = parse_arguments()
    
    # Format duration for display
    if args.scan_time >= 60:
        duration_str = f"{args.scan_time // 60}m {args.scan_time % 60}s" if args.scan_time % 60 else f"{args.scan_time // 60}m"
    else:
        duration_str = f"{args.scan_time}s"
    
    print("=" * 60)
    print("P2P Bitcoin Order Aggregator")
    print("=" * 60)
    print(f"Scan Duration: {duration_str}")
    print(f"Currency Filter: {', '.join(args.currencies) if len(args.currencies) < 10 else f'{len(args.currencies)} currencies'}")
    print()
    
    # Get exchange rates
    exchange_rates = update_exchange_rates()
    currency_filter = set(args.currencies)
    
    # Setup Nostr client
    print("Setting up Nostr client...")
    client = Client()
    
    for relay in NOSTR_RELAYS:
        try:
            await client.add_relay(relay)
            print(f"✓ Added relay: {relay}")
        except Exception as e:
            print(f"⚠ Failed to add relay {relay}: {e}")
    
    print("Connecting to relays...")
    await client.connect()
    print("✓ Connected to relays")
    
    # Create filter and subscribe
    p2p_filter = Filter().kind(Kind(38383))
    await client.subscribe(p2p_filter)
    print("✓ Subscribed to P2P events (kind 38383)")
    
    # Start scanning
    print(f"\nStarting {duration_str} scan...")
    print("Real-time updates:")
    print("-" * 80)
    
    scan_start = datetime.now(timezone.utc)
    scan_end = scan_start + timedelta(seconds=args.scan_time)
    
    orders = []
    processed_events = set()
    
    async def display_updater():
        """Background task to update display every second"""
        while datetime.now(timezone.utc) < scan_end:
            print_real_time_update(orders, len(orders), scan_start)
            await asyncio.sleep(1)
    
    async def event_processor():
        """Process Nostr events"""
        try:
            stream = await client.stream_events(p2p_filter, timedelta(seconds=10))
            
            while datetime.now(timezone.utc) < scan_end:
                try:
                    event = await asyncio.wait_for(stream.next(), timeout=2.0)
                    
                    if event is not None:
                        event_id = event.id().to_hex()
                        if event_id not in processed_events:
                            processed_events.add(event_id)
                            event_orders = process_event(event, exchange_rates, currency_filter)
                            
                            if event_orders:
                                orders.extend(event_orders)
                                
                except asyncio.TimeoutError:
                    continue
                
                except Exception:
                    # Stream error, restart
                    stream = await client.stream_events(p2p_filter, timedelta(seconds=10))
                    continue
        
        except Exception as e:
            print(f"Event processor error: {e}")
    
    try:
        # Run both tasks concurrently
        await asyncio.gather(
            display_updater(),
            event_processor()
        )
    
    except KeyboardInterrupt:
        print("\n\nScan interrupted by user.")
    except Exception as e:
        print(f"\n\nError during scan: {e}")
    
    finally:
        await client.disconnect()
    
    # Final results
    scan_end_actual = datetime.now(timezone.utc)
    
    # Clear the real-time display area properly
    print("\n" * 7)  # Move down past the real-time display
    print("=" * 80)
    print("SCAN COMPLETE")
    print("=" * 80)
    
    if orders:
        # Generate and display final report
        report = generate_final_report(orders, args.currencies, args.scan_time)
        print(report)
        
        # Write CSV file
        csv_filename = create_csv_filename(args.currencies)
        write_csv_file(orders, csv_filename, args.currencies, args.scan_time, scan_start, scan_end_actual)
    else:
        print("No matching orders found during scan period.")
        print(f"Filters applied: {', '.join(args.currencies)}")

if __name__ == "__main__":
    asyncio.run(main())
