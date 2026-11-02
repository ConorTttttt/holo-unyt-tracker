# HOLO → Unyt Bridge Tracker — unofficial community project

**Independent community-built dashboard. This is NOT an official Holo or Unyt tool and is not operated by, affiliated with, or endorsed by Holo, Unyt, or their teams.**

The static GitHub Pages site tracks the [Ethereum mainnet HOT bridge](https://etherscan.io/address/0x8334c5f8fdb1200d80210c9ccc314664b51a0bcd) using **actual `Lock` and `AdminWithdraw` smart-contract events** and the bridge's Raindex vault balance. It does not assume that every generic ERC-20 transfer is a deposit. Unyt issuance or HoloFuel conversion **cannot be independently confirmed** using only Ethereum data.

## Corrected metric definitions

| Dashboard metric | Source and meaning |
| --- | --- |
| **Currently locked** | `HoloLockVault.vaultBalance()` → HOT in the specific Raindex V6 vault owned by the bridge contract. NOT the HOT balance on the bridge's own Ethereum address. |
| **HOT supply bridged (%)** | The vault balance divided by Ethereum HOT ERC-20 `totalSupply()` × 100. Total supply is read on-chain, not hard-coded or equated with circulating supply. |
| **Total bridged in** | Sum of `Lock(address,uint256,bytes32,uint256)` events emitted by the bridge contract. Actual bridge lock calls, not arbitrary ERC-20 deposits. |
| **Unique locking wallets** | Count distinct Ethereum `Lock.sender` values, case-insensitive. A wallet counts once no matter how many locks. Contract wallets and multiple wallets belonging to one person are possible. This is **not** a unique Unyt-user count. |
| **Lock transactions** | Unique Ethereum transaction hashes that emitted one or more `Lock` events. The separate lock-event count captures multiple locks in one transaction. |
| **New locking wallets (7d)** | Ethereum sender addresses whose **first-ever** `Lock` event was within the last rolling 7 days (UTC). |
| **Total released from vault** | `sum(Lock amounts) - current vaultBalance()`. This is net HOT that has left the specific vault according to Ethereum-side accounting (may include exits by various supported flows). |
| **Emergency admin releases** | Sum of the bridge's explicit `AdminWithdraw(address,uint256,address)` events. |
| **Estimated claim releases** | `total released - emergency admin releases`. It is an **inference** from net vault accounting; NOT a directly verified list or count of individual coupon claims. The updater refuses to publish if reconciliation is impossible. |
| **Cumulative deposits chart** | Historical cumulative `Lock` events. **Not** a historical vault balance (historical claim timestamps cannot be derived from these events). |
| **Recent verified events** | Recent `Lock` and `AdminWithdraw` events. Individual coupon claims aren't listed: we do not invent per-claim data. |

The [HoloLockVault contract source](https://github.com/unytco/raindex-orders/blob/main/src/HoloLockVault.sol) shows `lock()` transferring HOT into a Raindex vault and emitting `Lock`, and `adminWithdraw()` emitting `AdminWithdraw`. Ordinary coupon redemptions operate through the Raindex claim order, rather than a `Claim` event on HoloLockVault. The current release total is thus useful, but coupon-claim counts or dates would require more specialized Raindex-event indexing and validation.

### Accuracy protections

- The updater checks the live bridge's `token()`, `orderbook()`, and `vaultId()` against the expected mainnet implementation, and confirms HOT's 18 decimals.
- Each complete history is cross-checked against the contract's live `lockNonce()` counter (one increment per successful `Lock`) to detect missing deposits or incomplete historical pagination.
- Event identity is **(transaction hash, log index)**. Etherscan's `logs/getLogs` includes the real log index. Missing indices are rejected; separate locks in one transaction are not conflated.
- The first run discovers the contract's deployment block, scans `Lock` and `AdminWithdraw` logs from deployment and stores an event cache in `data/events.json`. Following runs fetch only new blocks plus a 12-block overlap to reduce reorg risks.
- An Etherscan log result at its size limit is recursively split into smaller block ranges, and a saturated single block fails closed rather than silently dropping events. API errors or impossible vault accounting fail without overwriting displayed data.
- The workflow commits both `data/events.json` and `data/bridge.json` so the next refresh retains full wallet and deposit history. **Do not delete `data/events.json` casually**; removing it triggers a full history rescan.
- Dashboard figures are updated by GitHub Actions approximately every 30 minutes (scheduled Actions jobs can be delayed). The site uses the last successful snapshot.

**Snapshot caveat:** Etherscan's documented `eth_call` endpoint accepts only the `latest` block tag. The updater scans event logs to a recent head, rechecks that head, and fetches live vault and token readings. These queries are not truly atomic; a new lock/claim mined during a scan may cause temporary discrepancies. If accounting cannot reconcile, the update fails and keeps the previous published snapshot. A fully atomic historical snapshot would require an additional archive-capable Ethereum RPC endpoint.

**Bridge-credit caveat:** A valid Ethereum `Lock` event demonstrates that an Ethereum-side bridge lock executed, **not** that Unyt credited the mirrored HOT or that an account accepted it. It also does not identify individual people, ownership of several wallets, or HoloFuel swaps.

## Publish on GitHub Pages

1. Create a public GitHub repository such as `holo-unyt-tracker` (or use your existing repository).
2. Upload **all** files from this ZIP to the repository root, including `.github/workflows/update.yml`. For an existing deployment, replace the files while preserving the directory structure. Git `push` handles the hidden `.github` folder most reliably.
3. Get an [Etherscan API key](https://etherscan.io/myapikey), then go to **Settings → Secrets and variables → Actions** and set the repository secret `ETHERSCAN_API_KEY`. Do not put the key in source code.
4. Enable Actions workflow permissions for writing repository contents under **Settings → Actions → General → Workflow permissions → Read and write permissions** if your repository requires it.
5. Start **Actions → Update HOT bridge data → Run workflow**. The first run backfills historical logs, writes `data/events.json`, and generates `data/bridge.json`. Check the Actions log for API or reconciliation errors before relying on the metrics.
6. Under **Settings → Pages**, choose **Deploy from a branch → main → / (root)**.
7. The site will be at `https://YOUR-USERNAME.github.io/holo-unyt-tracker/` if you use those repository names. The new dashboard deliberately shows placeholders until a successful schema-version 2 data sync, rather than relabeling older approximate figures as verified.

No additional paid service or API secret is required beyond your existing Etherscan key *if the API endpoints and rate limits stay available on your Etherscan plan*. If Etherscan changes its access rules or the bridge's ABI or vault accounting changes, the workflow will need maintenance. GitHub Pages is public: the event cache also contains **public Ethereum wallet addresses** from the bridge events.

## Local tests and preview

```bash
python -m unittest discover -s tests -v
python -m http.server 8000
```

The preview will show placeholders until `update.py` has run with `ETHERSCAN_API_KEY` set. Run `python update.py` only with a working network connection and API key; it never signs or sends transactions.

## References

- [Official Net Zero published contract addresses](https://netzero.zone/parameters)
- [Unyt's Ethereum/HoloFuel bridge overview](https://netzero.zone/holofuel-launch.html)
- [HoloLockVault implementation](https://github.com/unytco/raindex-orders/blob/main/src/HoloLockVault.sol)
- [Raindex V6 mainnet deployment documentation](https://github.com/unytco/raindex-orders/blob/main/DEPLOY.md)
- [Etherscan event-log API](https://docs.etherscan.io/api-reference/endpoint/getlogs)
- [Etherscan eth_call API](https://docs.etherscan.io/api-reference/endpoint/ethcall)

### On-chain addresses

- HOT ERC-20: `0x6c6EE5e31d828De241282B9606C8e98Ea48526E2`
- HoloLockVault: `0x8334c5f8fdb1200d80210c9ccc314664b51a0bcd`
- Raindex V6 orderbook: `0x37FC0EFec37D19f8A221aa4F8F7600C9ba2AcD20`