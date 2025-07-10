# P2P Band Aggregator - Python Version

This script connects to Nostr relays to aggregate P2P Bitcoin exchange orders, similar to the p2p.band website. It fetches order data from Nostr and pricing data from public APIs.

## Attribution

This Python implementation is based on the original [p2pband](https://github.com/KoalaSat/p2pband) project by [KoalaSat](https://github.com/KoalaSat). The original project is a React-based web application that provides a similar P2P Bitcoin order aggregation service. This Python version reimplements the core functionality as a command-line tool for server-side usage and analysis.

**Original Project:** https://github.com/KoalaSat/p2pband  
**Original Author:** KoalaSat  
**License:** MIT (following the original project's license)

Thank you to KoalaSat for the inspiration and the excellent foundation that made this Python implementation possible.

## Setup

It is highly recommended to use a Python virtual environment to manage dependencies and avoid conflicts with other projects.

1.  **Create and activate a virtual environment:**

    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

2.  **Install the required dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

## Usage

To run the script, simply execute the following command:

```bash
python3 p2p_aggregator.py

<OR>

python3 p2p_aggregator.py -t 30s -c USD,BTC
```

## Help

To see the full list of arguments (scan time, currencies and cryptos supported), run:

```bash
python3 p2p_aggregator.py -h
```

The script will start fetching and processing events from Nostr relays and will print the structured order data to the console, saving a CSV report at the end, e.g. 'offers-ALL-20250710181005.csv'
