"""Offline regression tests; no Ethereum credentials or internet required."""
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('update', Path(__file__).resolve().parents[1] / 'update.py')
tracker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracker)

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
A = '0x' + 'a' * 40
B = '0x' + 'b' * 40
C = '0x' + 'c' * 40
RECIPIENT = '0x' + 'd' * 40
HOT = 10 ** 18


def tstamp(iso):
    return int(datetime.fromisoformat(iso).timestamp())


def word(n):
    return format(n, '064x')


def indexed_address(a):
    return '0x' + word(int(a, 16))


def lock(tx, ix, block, when, sender, amount, lockid):
    return {'address': tracker.BRIDGE, 'topics': [tracker.LOCK_TOPIC, indexed_address(sender), '0x' + '01'*32],
            'data': '0x' + word(amount * HOT) + word(lockid), 'logIndex': hex(ix),
            'transactionHash': '0x' + tx*64, 'blockNumber': hex(block), 'timeStamp': hex(tstamp(when))}


def admin(tx, ix, block, when, amount):
    return {'address': tracker.BRIDGE, 'topics': [tracker.ADMIN_TOPIC, indexed_address(A), indexed_address(RECIPIENT)],
            'data': '0x' + word(amount * HOT), 'logIndex': hex(ix),
            'transactionHash': '0x' + tx*64, 'blockNumber': hex(block), 'timeStamp': hex(tstamp(when))}


ROWS = [
    lock('1', 0, 102, '2026-10-20T12:00:00+00:00', A, 50, 1),
    lock('2', 1, 115, '2026-10-30T12:00:00+00:00', B, 100, 2),
    lock('2', 2, 115, '2026-10-30T12:00:00+00:00', C, 25, 3),  # same TX, distinct log
    lock('3', 0, 120, '2026-11-01T10:00:00+00:00', A, 10, 4),
    admin('4', 6, 132, '2026-11-02T08:00:00+00:00', 5),
]


def sample_api(rows=ROWS, vault=130, supply=10000, heads=(145, 145), token=tracker.TOKEN,
               orderbook=tracker.ORDERBOOK, deployed=100):
    seen = {'head': 0}
    def handler(p):
        action = p['action']
        if action == 'eth_call':
            assert p['tag'] == 'latest'  # Etherscan's endpoint does not support block tags
            selector = p['data']
            values = {
                tracker.TOKEN_SELECTOR: int(token, 16),
                tracker.ORDERBOOK_SELECTOR: int(orderbook, 16),
                tracker.VAULT_ID_SELECTOR: 123,
                tracker.LOCK_NONCE_SELECTOR: sum(r['topics'][0] == tracker.LOCK_TOPIC for r in rows),
                tracker.DECIMALS_SELECTOR: 18,
                tracker.VAULT_BALANCE: vault * HOT,
                tracker.SUPPLY_SELECTOR: supply * HOT,
            }
            if selector not in values:
                raise AssertionError(f'Unexpected selector {selector}')
            return '0x' + word(values[selector])
        if action == 'getcontractcreation':
            return [{'contractAddress': tracker.BRIDGE, 'blockNumber': str(deployed)}]
        if action == 'eth_blockNumber':
            ix = min(seen['head'], len(heads)-1)
            seen['head'] += 1
            return hex(heads[ix])
        if action == 'getLogs':
            return [row for row in rows if row['topics'][0] == p['topic0'] and
                    p['fromBlock'] <= int(row['blockNumber'], 16) <= p['toBlock']]
        raise AssertionError(f'Unexpected request: {p}')
    return handler


class TestEncoding(unittest.TestCase):
    def test_eth_contract_selectors_and_event_topics_independent_keccak_vectors(self):
        # Generated using Ethereum Keccak-256, not hashlib.sha3_256.
        self.assertEqual(tracker.LOCK_TOPIC, '0x48ea0306c5a885fdcf70304ba5cc3ded955d5ab2fb6c202d07efb49d6698dafc')
        self.assertEqual(tracker.VAULT_BALANCE, '0x0bf6cc08')
        self.assertEqual(tracker.SUPPLY_SELECTOR, '0x18160ddd')

    def test_log_decoder_is_event_based_not_generic_transfers(self):
        info = tracker.decode_log(ROWS[0])
        self.assertEqual(info['type'], 'lock')
        self.assertEqual(info['sender'], A)
        self.assertEqual(info['amountRaw'], str(50 * HOT))
        self.assertEqual(info['lockId'], 1)
        bad = {**ROWS[0], 'topics': ['0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef']}
        with self.assertRaises(RuntimeError):
            tracker.decode_log(bad)

    def test_missing_log_index_fails_no_fallback_zero(self):
        with self.assertRaisesRegex(RuntimeError, 'logIndex'):
            tracker.decode_log({k:v for k,v in ROWS[1].items() if k!='logIndex'})

    def test_decode_two_distinct_locks_in_one_tx(self):
        b, c = tracker.decode_log(ROWS[1]), tracker.decode_log(ROWS[2])
        self.assertEqual(b['hash'], c['hash'])
        self.assertNotEqual(b['logIndex'], c['logIndex'])
        self.assertNotEqual(b['sender'], c['sender'])


class TestSnapshots(unittest.TestCase):
    def test_reconciles_vault_claims_and_admin_withdrawals(self):
        cache = {'schemaVersion':1, 'fromBlock':100,'toBlock':145,
                 'events':[tracker.decode_log(r) for r in ROWS]}
        result=tracker.build_metrics(cache, 130 * HOT, 10000 * HOT, NOW)
        self.assertEqual(result['currentLocked'], '130')
        self.assertEqual(result['totalDeposited'], '185')
        self.assertEqual(result['totalWithdrawn'], '55')
        self.assertEqual(result['estimatedClaimed'], '50')
        self.assertEqual(result['adminWithdrawn'], '5')
        self.assertEqual(result['percentOfSupplyLocked'], '1.300')
        self.assertEqual(result['uniqueDepositingWallets'], 3)
        self.assertEqual(result['depositTransfers'], 4)
        self.assertEqual(result['depositTransactions'], 3)
        self.assertEqual(result['adminWithdrawalEvents'], 1)
        self.assertIsNone(result['withdrawalTransfers'])
        self.assertEqual(result['newDepositingWallets7d'], 2)
        self.assertEqual(result['timeline'][-1]['cumulativeDeposited'], '185')
        self.assertNotIn('netLocked', result['timeline'][-1])
        self.assertEqual(result['walletTimeline'][-1]['uniqueWallets'], 3)

    def test_zero_supply_and_impossible_vault_accounting_fail(self):
        cache = {'schemaVersion':1, 'fromBlock':100, 'toBlock':145,
                 'events':[tracker.decode_log(r) for r in ROWS]}
        with self.assertRaisesRegex(RuntimeError, 'total supply'):
            tracker.build_metrics(cache, 130 * HOT, 0, NOW)
        with self.assertRaisesRegex(RuntimeError, 'accounting'):
            tracker.build_metrics(cache, 184 * HOT, 10000 * HOT, NOW)  # 1 outflow, but 5 admin
        with self.assertRaisesRegex(RuntimeError, 'accounting'):
            tracker.build_metrics(cache, 200 * HOT, 10000 * HOT, NOW)  # more than locked

    def test_empty_bridge_still_produces_valid_empty_series(self):
        result = tracker.build_metrics({'schemaVersion':1,'fromBlock':100,'toBlock':145,'events':[]},0,10000*HOT,NOW)
        self.assertEqual(result['uniqueDepositingWallets'],0)
        self.assertEqual(result['totalWithdrawn'],'0')
        self.assertEqual(result['timeline'],[])

    def test_full_initial_refresh_writes_cache_and_frontend_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)/'data'/'bridge.json'
            cache = Path(folder)/'data'/'events.json'
            with patch.object(tracker,'CACHE',cache),patch.object(tracker,'OUT',out),\
                 patch.object(tracker,'request',side_effect=sample_api()),patch.object(tracker.time,'sleep'):
                tracker.main()
            state=json.loads(cache.read_text())
            metric=json.loads(out.read_text())
            self.assertEqual(state['fromBlock'],100)
            self.assertEqual(state['toBlock'],145)
            self.assertEqual(len(state['events']),5)
            self.assertEqual(metric['totalDeposited'],'185')
            self.assertEqual(metric['uniqueDepositingWallets'],3)
            self.assertEqual(metric['estimatedClaimed'],'50')

    def test_repeat_refresh_preserves_all_time_wallet_counts_no_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bridge.json'; cache=Path(folder)/'events.json'
            patches=[patch.object(tracker,'CACHE',cache),patch.object(tracker,'OUT',out),patch.object(tracker.time,'sleep')]
            with patches[0],patches[1],patches[2]:
                with patch.object(tracker,'request',side_effect=sample_api()):
                    tracker.main()
                with patch.object(tracker,'request',side_effect=sample_api(heads=(151,151))):
                    tracker.main()
            state=json.loads(cache.read_text()); metrics=json.loads(out.read_text())
            self.assertEqual(state['toBlock'],151)
            self.assertEqual(len(state['events']),5)
            self.assertEqual(metrics['uniqueDepositingWallets'],3)

    def test_wrong_contract_token_never_overwrites_published_data(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bridge.json'; out.write_text('{"existing":true}')
            cache=Path(folder)/'events.json'
            with patch.object(tracker,'CACHE',cache),patch.object(tracker,'OUT',out),\
                 patch.object(tracker,'request',side_effect=sample_api(token=A)),patch.object(tracker.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'configured HOT token'):
                    tracker.main()
            self.assertFalse(cache.exists())
            self.assertEqual(out.read_text(),'{"existing":true}')

    def test_mismatched_onchain_lock_nonce_prevents_misleading_wallets(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bridge.json'; out.write_text('{"existing":true}')
            cache=Path(folder)/'events.json'
            api=sample_api(rows=ROWS[:3], vault=130, heads=(145,145,145,145,145))
            # Scenario: chain indicates more locks than the explorer returned.
            def stale_log_api(p):
                if p.get('data') == tracker.LOCK_NONCE_SELECTOR:
                    return '0x' + word(4)
                return api(p)
            with patch.object(tracker,'CACHE',cache), patch.object(tracker,'OUT',out), \
                 patch.object(tracker,'request',side_effect=stale_log_api), patch.object(tracker.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'Lock history incomplete'):
                    tracker.main()
            self.assertFalse(cache.exists())
            self.assertEqual(out.read_text(),'{"existing":true}')

    def test_unreconciled_vault_never_overwrites_published_data(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'bridge.json'; out.write_text('{"existing":true}')
            cache=Path(folder)/'events.json'
            with patch.object(tracker,'CACHE',cache),patch.object(tracker,'OUT',out),\
                 patch.object(tracker,'request',side_effect=sample_api(vault=195)),patch.object(tracker.time,'sleep'):
                with self.assertRaisesRegex(RuntimeError, 'accounting'):
                    tracker.main()
            self.assertFalse(cache.exists())
            self.assertEqual(out.read_text(),'{"existing":true}')


class TestPagination(unittest.TestCase):
    def test_full_window_splits_and_deduplicates_real_log_indices(self):
        original=tracker.MAX_LOGS
        try:
            tracker.MAX_LOGS=2
            rows=[lock('6',0,100,'2026-11-01T10:00:00+00:00',A,10,1),
                  lock('6',1,101,'2026-11-01T10:00:00+00:00',B,10,2),
                  lock('7',0,102,'2026-11-01T10:00:00+00:00',C,10,3)]
            def fake(p):
                return [r for r in rows if p['fromBlock']<=int(r['blockNumber'],16)<=p['toBlock']][:2]
            with patch.object(tracker,'request',side_effect=fake):
                result=tracker.fetch_logs(tracker.LOCK_TOPIC,100,103)
            self.assertEqual(len(result),3)
        finally:
            tracker.MAX_LOGS=original

    def test_capped_single_block_aborts_instead_of_truncating(self):
        with patch.object(tracker,'MAX_LOGS',2),patch.object(tracker,'request',return_value=[ROWS[1],ROWS[2]]):
            with self.assertRaisesRegex(RuntimeError,'cannot guarantee complete history'):
                tracker.fetch_logs(tracker.LOCK_TOPIC,115,115)

    def test_retry_overlap_replaces_reorged_logs(self):
        original = {'schemaVersion':1,'fromBlock':100,'toBlock':140,'events':[tracker.decode_log(r) for r in ROWS]}
        # Reorg removes the admin transaction at block 132, adds a new admin event.
        changed = ROWS[:4] + [admin('9',5,132,'2026-11-02T08:00:00+00:00',7)]
        def response(p):
            return [r for r in changed if r['topics'][0] == p['topic0'] and
                    p['fromBlock'] <= int(r['blockNumber'],16) <= p['toBlock']]
        with patch.object(tracker,'request',side_effect=response),patch.object(tracker.time,'sleep'):
            refreshed = tracker.ingest(original,100,145)
        self.assertEqual(len(refreshed['events']),5)
        self.assertIn('9'*64, [x['hash'][2:] for x in refreshed['events']])
        self.assertNotIn('4'*64, [x['hash'][2:] for x in refreshed['events']])


if __name__=='__main__':
    unittest.main()