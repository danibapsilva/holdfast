# Security

Holdfast runs on test networks only, and its tokens have no value. Still, it's built the way an escrow
holding real money has to be, so a bug in it is worth hearing about.

## Reporting

Email **silva.d4@northeastern.edu** with what you found and how to reproduce it. Please don't open a public
issue for anything that could let someone take funds, skip a check, or reach a network other than Sepolia.
You'll get a reply within a few days.

## In scope

- Getting a deal paid out twice, to the wrong party, or before it's confirmed.
- Crediting one on-chain transfer to more than one deal, or more than once.
- Getting Holdfast to sign or send anything on a network other than Sepolia.
- Leaking the seed phrase or any derived key.

## Out of scope

- The value of test tokens (there is none).
- Rate limits on public demo endpoints, and anything that needs control of the server already.

The design behind these protections is in [docs/threat-model.md](docs/threat-model.md).
