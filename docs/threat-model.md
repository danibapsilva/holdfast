# Threat model

Holdfast only runs on test networks, so nothing here protects real money. It's written as if it did,
because the point of the project is to show how an escrow should be built.

| What could go wrong | What stops it |
| --- | --- |
| **It runs against a real network.** A wrong RPC URL, a copied config, a typo. | `require_testnet` refuses any chain id not on the allowlist (Sepolia only), when the adapter starts and again before every send. The tUSD contract's constructor refuses any network but Sepolia or a local chain. CI fails if a mainnet RPC host, the mainnet explorer, or chain id 1 appears anywhere in the repo. |
| **The same payment is credited twice.** Two deals see one transfer, or a restart re-reads the chain. | Payments are keyed by transaction hash and log index. The deal ignores a transfer it already has (only confirmations update), and the store's `claim_transfer` records which deal owns each transfer globally, so a second deal flags it for review instead of counting it. |
| **A shallow reorg undoes a payment.** | A payment only funds a deal after `required_confirmations` (default 2 on Sepolia). Confirmations never go down in the deal's record; a reorg deeper than that is out of scope for a test network and would need a person. |
| **A payout is sent twice.** A crash after sending but before saving, then a retry. | `begin_payout` is saved before any transfer goes out, and a started payout is never started again. A failed or interrupted send flags the deal for a person to check the chain. |
| **Two workers act on the same deal at once.** | Optimistic locking: every save checks the version the deal was loaded at. The loser gets `Conflict` and reloads. |
| **The wrong person acts.** The seller releases to themselves, a stranger cancels. | Every action is checked against the transition table for the caller's role in that deal, and someone outside the deal has no role. |
| **Payment to the wrong place, or the wrong token.** | Each deal has its own deposit address. The Sepolia adapter only reads the configured tUSD contract and refuses any other token address. Late payments to a closed deal are set aside and flagged. |
| **A bug drains the demo treasury.** | Per-deal and per-day caps are checked before deals are created and before any send. tUSD minting is capped per call and only the treasury can mint. |
| **The seed leaks.** | The seed lives only in an environment variable, is never printed (`repr` hides it), and was made fresh for the demo, so it has never held real money. `.gitignore` excludes env files and key material, and gitleaks scans every push. |
| **Griefing in the demo.** Opening many deals, never paying. | Unpaid deals expire at the payment deadline. Phase 2 adds rate limits per visitor and one open deal per person in the API and the bot. |
| **Someone copies the code to run a paid escrow.** | The PolyForm Noncommercial license forbids commercial use. The public code also leaves out what a business needs: mainnet adapters, fee and payout logic, fraud rules, and multi-provider payment detection. |

## Out of scope

- Real money, mainnet networks, and anything custodial for the public.
- Deep chain reorganizations and RPC providers lying about chain state.
- The private production system, which shares no code, keys, data, or infrastructure with this one.
