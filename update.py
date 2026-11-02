#!/usr/bin/env python3
"""Build an unofficial HOT -> Unyt Ethereum bridge snapshot from contract events.

Requires Etherscan API V2 and keeps a compact on-chain event cache in GitHub.
A Lock is a bridge deposit; *ordinary* token Transfers are NOT deposits.
Coupon claim totals are inferred from the vault's accounting, NOT attributed to
individual claim transactions. Emergency admin releases have their own event.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

BRIDGE = '0x8334c5f8fdb1200d80210c9ccc314664b51a0bcd'
TOKEN = '0x6c6ee5e31d828de241282b9606c8e98ea48526e2'
ORDERBOOK = '0x37fc0efec37d19f8a221aa4f8f7600c9ba2acd20'
DECIMALS = 18
API = 'https://api.etherscan.io/v2/api'
ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data' / 'bridge.json'
CACHE = ROOT / 'data' / 'events.json'
KEY = os.environ.get('ETHERSCAN_API_KEY', '').strip()

# Keccak-256 event signatures (Ethereum Keccak, NOT NIST SHA3-256).
# Lock(address,uint256,bytes32,uint256) -- see src/HoloLockVault.sol
LOCK_TOPIC = '0x48ea0306c5a885fdcf70304ba5cc3ded955d5ab2fb6c202d07efb49d6698dafc'
ADMIN_TOPIC = '0x164950a2c48527c1c106a7b324cc131b87a07bccb9100a166b60b7a4f2089e0a'
# Function selectors: vaultBalance(), token(), orderbook(), vaultId().
VAULT_BALANCE = '0x0bf6cc08'
TOKEN_SELECTOR = '0xfc0c546a'
ORDERBOOK_SELECTOR = '0xc18b1d5e'
VAULT_ID_SELECTOR = '0x33194c0a'
LOCK_NONCE_SELECTOR = '0xb5a9096e'  # lockNonce(): counts emitted Lock events
SUPPLY_SELECTOR = '0x18160ddd'  # ERC-20 totalSupply()
DECIMALS_SELECTOR = '0x313ce567'  # ERC-20 decimals()
CHUNK_BLOCKS = 2000  # Limits log calls; split further whenever response fills.
MAX_LOGS = 1000
OVERLAP_BLOCKS = 12
HEX64 = re.compile(r'^0x[0-9a-fA-F]{64}$')
HEX40 = re.compile(r'^0x[0-9a-fA-F]{40}$')


def request(params: dict):
    """Call Etherscan; errors must not silently turn into empty history."""
    if not KEY:
        raise RuntimeError('Missing ETHERSCAN_API_KEY GitHub Actions secret')
    query = {'chainid': '1', 'apikey': KEY, **params}
    url = API + '?' + urllib.parse.urlencode(query)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers={'User-Agent': 'community-holo-unyt-tracker/2.0'}), timeout=35
            ) as response:
                body = json.load(response)
            if not isinstance(body, dict):
                raise RuntimeError('Unexpected API response')
            if body.get('status') == '1' or (body.get('jsonrpc') == '2.0' and body.get('error') is None):
                return body['result']
            message = str(body.get('message', '')) + ' ' + str(body.get('result', body.get('error', '')))
            if params.get('module') == 'logs' and ('No records found' in message or 'No logs found' in message):
                return []
            raise RuntimeError(message)
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
            if attempt == 4:
                raise RuntimeError(f'Etherscan {params.get("action")} failed: {exc}') from exc
            time.sleep(min(12, 2 ** attempt))
    raise AssertionError('Unreachable')


def integer(value):
    if isinstance(value, bool):
        raise ValueError('Invalid numeric field')
    if isinstance(value, int):
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f'Invalid numeric field: {value!r}')
    return int(value, 16 if value.startswith('0x') else 10)


def fmt(raw):
    return format(Decimal(raw) / Decimal(10 ** DECIMALS), 'f')


def call_latest(address: str, selector: str):
    result = request({'module': 'proxy', 'action': 'eth_call', 'to': address,
                      'data': selector, 'tag': 'latest'})
    if not isinstance(result, str) or not HEX64.fullmatch(result):
        raise RuntimeError(f'Invalid eth_call result for {selector}: {result!r}')
    return int(result, 16)


def checked_contract():
    token_raw = call_latest(BRIDGE, TOKEN_SELECTOR)
    orderbook_raw = call_latest(BRIDGE, ORDERBOOK_SELECTOR)
    vault_id = call_latest(BRIDGE, VAULT_ID_SELECTOR)
    if '0x' + f'{token_raw:040x}'[-40:] != TOKEN:
        raise RuntimeError('Bridge token() does not match configured HOT token; refusing data update')
    if '0x' + f'{orderbook_raw:040x}'[-40:] != ORDERBOOK:
        raise RuntimeError('Bridge orderbook() does not match expected Raindex V6 deployment')
    if vault_id == 0:
        raise RuntimeError('Bridge vaultId() is zero')
    decimals = call_latest(TOKEN, DECIMALS_SELECTOR)
    if decimals != DECIMALS:
        raise RuntimeError(f'HOT token decimals changed: {decimals}')
    return vault_id


def creation_block():
    result = request({'module': 'contract', 'action': 'getcontractcreation',
                      'contractaddresses': BRIDGE})
    if not isinstance(result, list) or len(result) != 1:
        raise RuntimeError('Cannot determine bridge deployment block')
    row = result[0]
    if str(row.get('contractAddress', '')).lower() != BRIDGE:
        raise RuntimeError('Contract-creation response does not match the configured bridge')
    return integer(row['blockNumber'])


def fetch_logs(topic: str, start: int, end: int):
    """Fetch every log, recursively splitting any full or rejected interval.

    This avoids both the usual 1,000-log response cap and silent pagination loss.
    """
    if start > end:
        return []
    params = {'module': 'logs', 'action': 'getLogs', 'address': BRIDGE,
              'topic0': topic, 'fromBlock': start, 'toBlock': end,
              'page': 1, 'offset': MAX_LOGS}
    try:
        rows = request(params)
    except RuntimeError as exc:
        # Do NOT split an API-key/rate-limit/network outage into thousands of retries.
        message = str(exc).lower()
        range_error = any(s in message for s in ('block range', 'too many results', 'result window', 'query timeout'))
        if start == end or not range_error:
            raise
        mid = (start + end) // 2
        return fetch_logs(topic, start, mid) + fetch_logs(topic, mid + 1, end)
    if not isinstance(rows, list):
        raise RuntimeError(f'Expected an array of logs, got {type(rows).__name__}')
    if len(rows) >= MAX_LOGS:
        if start == end:
            raise RuntimeError(f'{MAX_LOGS}+ events in block {start}; cannot guarantee complete history')
        mid = (start + end) // 2
        return fetch_logs(topic, start, mid) + fetch_logs(topic, mid + 1, end)
    return rows


def decode_log(log: dict):
    if not isinstance(log, dict) or str(log.get('address', '')).lower() != BRIDGE:
        raise RuntimeError('Malformed or foreign bridge log')
    topics = log.get('topics', [])
    if not isinstance(topics, list) or not topics:
        raise RuntimeError('Missing event topic')
    topic = topics[0].lower()
    if topic not in (LOCK_TOPIC, ADMIN_TOPIC):
        raise RuntimeError('Unexpected bridge event')
    if len(topics) != 3 or not all(isinstance(t, str) and HEX64.fullmatch(t) for t in topics):
        raise RuntimeError('Malformed indexed event arguments')
    tx_hash = log.get('transactionHash', '')
    if not isinstance(tx_hash, str) or not HEX64.fullmatch(tx_hash):
        raise RuntimeError('Missing transaction hash')
    # A log index is mandatory. Etherscan tokentx can omit it; getLogs must not.
    if log.get('logIndex', '') in ('', '0x', None):
        raise RuntimeError(f'Missing logIndex for transaction {tx_hash}')
    index = integer(log['logIndex'])
    block = integer(log['blockNumber'])
    stamp = integer(log['timeStamp'])
    if stamp <= 0:
        raise RuntimeError('Invalid block timestamp')
    data = log.get('data', '')
    if not isinstance(data, str) or not data.startswith('0x'):
        raise RuntimeError('Invalid event data')
    if topic == LOCK_TOPIC:
        if len(data) != 2 + 64 * 2:
            raise RuntimeError('Lock event data must contain amount and lockId')
        sender = '0x' + topics[1][-40:].lower()
        if not HEX40.fullmatch(sender) or int(sender[2:], 16) == 0:
            raise RuntimeError('Invalid Lock sender')
        amount = int(data[2:66], 16)
        lock_id = int(data[66:130], 16)
        if amount <= 0:
            raise RuntimeError('Nonpositive lock amount')
        return {'type': 'lock', 'hash': tx_hash.lower(), 'logIndex': index, 'blockNumber': block,
                'timestamp': stamp, 'sender': sender, 'amountRaw': str(amount), 'lockId': lock_id}
    if len(data) != 2 + 64:
        raise RuntimeError('AdminWithdraw event data must be one uint256 amount')
    amount = int(data[2:], 16)
    if amount <= 0:
        raise RuntimeError('Nonpositive admin withdrawal')
    return {'type': 'admin', 'hash': tx_hash.lower(), 'logIndex': index, 'blockNumber': block,
            'timestamp': stamp, 'recipient': '0x' + topics[2][-40:].lower(), 'amountRaw': str(amount)}


def load_cache():
    if not CACHE.exists():
        return None
    saved = json.loads(CACHE.read_text(encoding='utf-8'))
    if saved.get('schemaVersion') != 1 or not isinstance(saved.get('events'), list):
        raise RuntimeError('Unrecognized events cache schema; refuse to use partial historical data')
    if not isinstance(saved.get('toBlock'), int) or not isinstance(saved.get('fromBlock'), int):
        raise RuntimeError('Incomplete historical cache; remove data/events.json and resync')
    if saved['fromBlock'] < 0 or saved['toBlock'] < saved['fromBlock']:
        raise RuntimeError('Invalid cache block range')
    return saved


def ingest(existing, deployed: int, snapshot: int):
    if existing is not None:
        if existing['fromBlock'] != deployed:
            raise RuntimeError('Cached deployment block differs from the live contract')
        if snapshot < existing['toBlock']:
            raise RuntimeError('Ethereum snapshot is older than cached history')
        start = max(deployed, existing['toBlock'] - OVERLAP_BLOCKS + 1)
        retained = [ev for ev in existing['events'] if integer(ev['blockNumber']) < start]
    else:
        start = deployed
        retained = []
    logs = []
    for chunk_start in range(start, snapshot + 1, CHUNK_BLOCKS):
        chunk_end = min(chunk_start + CHUNK_BLOCKS - 1, snapshot)
        for topic in (LOCK_TOPIC, ADMIN_TOPIC):
            logs.extend(fetch_logs(topic, chunk_start, chunk_end))
        # Free API: avoid 5 queries / second, and stay predictable.
        time.sleep(0.22)
    new_events = [decode_log(log) for log in logs]
    merged = {}
    for ev in retained + new_events:
        ident = (ev['hash'], integer(ev['logIndex']))
        if ident in merged and merged[ident] != ev:
            raise RuntimeError(f'Conflicting event identity for {ident}')
        merged[ident] = ev
    events = sorted(merged.values(), key=lambda ev: (integer(ev['blockNumber']), integer(ev['logIndex'])))
    for ev in events:
        if not deployed <= integer(ev['blockNumber']) <= snapshot:
            raise RuntimeError('Out-of-range event in cache')
    return {'schemaVersion': 1, 'fromBlock': deployed, 'toBlock': snapshot, 'events': events}


def build_metrics(cache, vault_raw: int, supply_raw: int, now: datetime):
    if supply_raw <= 0:
        raise RuntimeError('HOT total supply is zero')
    if vault_raw < 0 or vault_raw > supply_raw:
        raise RuntimeError('Vault balance is negative or exceeds total supply')
    seven_days = now - timedelta(days=7)
    total_lock = total_admin = 0
    lock_hashes = set()
    first_sender = {}
    daily = defaultdict(lambda: {'inRaw': 0, 'newWallets': 0})
    recent = []
    for event in cache['events']:
        val = integer(event['amountRaw'])
        timestamp = datetime.fromtimestamp(integer(event['timestamp']), tz=timezone.utc)
        if timestamp > now + timedelta(minutes=3):
            raise RuntimeError('Event timestamp is unexpectedly in the future')
        day = timestamp.date().isoformat()
        if event['type'] == 'lock':
            total_lock += val
            daily[day]['inRaw'] += val
            lock_hashes.add(event['hash'])
            address = event['sender'].lower()
            if address not in first_sender:
                first_sender[address] = timestamp
                daily[day]['newWallets'] += 1
            direction = 'in'
            counterparty = address
        elif event['type'] == 'admin':
            total_admin += val
            direction = 'admin'
            counterparty = event['recipient']
        else:
            raise RuntimeError('Unknown cached event type')
        recent.append({'hash': event['hash'], 'direction': direction, 'amount': fmt(val),
                       'date': timestamp.isoformat(), 'counterparty': counterparty})
    # Vault accounting: tokens can leave through signed claims or adminWithdraw().
    # Counts/timestamps for individual claims are NOT inferable from Lock events.
    # Fail closed if other activity breaks this reconciliation assumption.
    total_outflow = total_lock - vault_raw
    claim_residual = total_outflow - total_admin
    if total_outflow < 0 or claim_residual < 0:
        raise RuntimeError('Vault accounting does not reconcile: locked minus vault balance '
                           'is smaller than recorded emergency withdrawals; refusing to publish')
    locked_progress = 0
    wallet_progress = 0
    timeline = []
    wallets = []
    for day in sorted(daily):
        locked_progress += daily[day]['inRaw']
        wallet_progress += daily[day]['newWallets']
        timeline.append({'date': day, 'cumulativeDeposited': fmt(locked_progress),
                         'deposited': fmt(daily[day]['inRaw'])})
        wallets.append({'date': day, 'uniqueWallets': wallet_progress,
                        'newWallets': daily[day]['newWallets']})
    share = Decimal(vault_raw) / Decimal(supply_raw) * 100
    return {
        'schemaVersion': 2,
        'updatedAt': now.isoformat(), 'source': 'Ethereum mainnet / Etherscan API V2',
        'bridge': BRIDGE, 'token': TOKEN, 'orderbook': ORDERBOOK, 'decimals': DECIMALS,
        'snapshotBlock': cache['toBlock'], 'scanFromBlock': cache['fromBlock'],
        'lockCounterChecked': True,
        'metricsMethod': 'Verified Lock and AdminWithdraw logs; live vaultBalance; claims inferred from vault reconciliation',
        'currentLocked': fmt(vault_raw), 'totalSupply': fmt(supply_raw),
        'percentOfSupplyLocked': format(share, 'f'),
        'totalDeposited': fmt(total_lock),
        'totalWithdrawn': fmt(total_outflow),
        'estimatedClaimed': fmt(claim_residual),
        'adminWithdrawn': fmt(total_admin),
        'depositTransfers': sum(e['type'] == 'lock' for e in cache['events']),
        'depositTransactions': len(lock_hashes),
        'uniqueDepositingWallets': len(first_sender),
        'newDepositingWallets7d': sum(t >= seven_days for t in first_sender.values()),
        'adminWithdrawalEvents': sum(e['type'] == 'admin' for e in cache['events']),
        'withdrawalTransfers': None,  # Per-coupon claim counts unavailable from these logs.
        'trackedEvents': len(cache['events']),
        'timeline': timeline, 'walletTimeline': wallets,
        'recentTransactions': recent[-20:][::-1],
        'note': ('Ethereum Lock senders are distinct addresses, not verified Unyt accounts or people. '
                 'HOT exited from the vault = Lock events - current vaultBalance(); admin exits come from '
                 'AdminWithdraw events; the remainder is an inferred claim total, not a direct count '
                 'of individual coupon claims. Historical chart shows cumulative locks, not historical vault balance.')
    }


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(path.name + '.tmp')
    staging.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    staging.replace(path)


def main():
    # Etherscan eth_call currently supports only the 'latest' tag, so the
    # event scan and balance reads cannot form an atomic historical snapshot.
    # Re-fetch the new head after the potentially long initial backfill, and
    # overlap recent blocks on each run to handle normal chain reorganisations.
    checked_contract()
    deployed = creation_block()
    head = integer(request({'module': 'proxy', 'action': 'eth_blockNumber'}))
    if deployed > head:
        raise RuntimeError('Bridge contract is not yet deployed at head block')
    cache = ingest(load_cache(), deployed, head)
    newer_head = integer(request({'module': 'proxy', 'action': 'eth_blockNumber'}))
    if newer_head < head:
        raise RuntimeError('Ethereum RPC head moved backwards during sync')
    if newer_head > head:
        cache = ingest(cache, deployed, newer_head)
    # Every successful lock() increments lockNonce exactly once. Comparing it
    # against the event count is a strong check that history was not truncated.
    for attempt in range(3):
        observed_locks = sum(ev['type'] == 'lock' for ev in cache['events'])
        onchain_lock_nonce = call_latest(BRIDGE, LOCK_NONCE_SELECTOR)
        if observed_locks == onchain_lock_nonce:
            break
        if attempt == 2:
            raise RuntimeError(f'Lock history incomplete: {observed_locks} events vs '
                               f'lockNonce() {onchain_lock_nonce}; refusing to publish')
        next_head = integer(request({'module': 'proxy', 'action': 'eth_blockNumber'}))
        if next_head < cache['toBlock']:
            raise RuntimeError('Ethereum RPC head moved backwards while validating locks')
        cache = ingest(cache, deployed, next_head)
    vault_raw = call_latest(BRIDGE, VAULT_BALANCE)
    supply_raw = call_latest(TOKEN, SUPPLY_SELECTOR)
    metrics = build_metrics(cache, vault_raw, supply_raw, datetime.now(timezone.utc))
    # Only persist once ALL API calls, decoding, and reconciliation succeed.
    write_json(CACHE, cache)
    write_json(OUT, metrics)
    print(f'Scanned through block {cache["toBlock"]} | latest vault {fmt(vault_raw)} HOT | '
          f'{metrics["uniqueDepositingWallets"]} Lock senders | '
          f'{metrics["depositTransfers"]} locks | estimated non-admin exits {metrics["estimatedClaimed"]} HOT')


if __name__ == '__main__':
    main()