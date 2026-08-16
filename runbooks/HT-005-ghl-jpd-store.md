# HT-005 — Create the JPD store in GoHighLevel

> ## ✅ COMPLETE — 2026-08-09
>
> Store **`JPD`** exists, id **`PhxzRjZIIfHmC06vqj8b`**, location
> `Zf5YnZeLjmXO7HrU6OUp`. Confirmed by the operator in the GHL UI, with the
> three test products listed inside it.
>
> - `jpd commerce contract-test` → `probe ok · GET /products/ -> 200` ·
>   `shape ok, 5 products` · **`ghl_payments is now live`**
> - A real 3-tier ladder was created at 1.00 / 4.00 / 13.00 (ratios 1× / 4× /
>   13×, inside DEC-001), each product excluded from **both** other stores and
>   confirmed by read-back
> - All three product pages return HTTP 200 with Add to Cart / Buy now / Stripe
>
> ⚠️ **Still outstanding for the phase-1 exit criterion:** a REAL purchase of
> each tier. The stub provider proves the code; only a real card proves the
> integration. Then delete the `JPD TEST — store-verify-20260809` products and
> `DELETE FROM solutions WHERE id = 9`.
>
> ### 🔴 Lesson — how NOT to check whether the store exists
>
> On 2026-08-09 an agent concluded this store did **not** exist, because
> `PhxzRjZIIfHmC06vqj8b` appeared nowhere in the union of every product's
> `excludedStoreIds`. **That inference is wrong and cost a round trip.**
>
> GHL records only the stores a product is EXCLUDED from. A store therefore
> becomes visible in that data only once some product excludes it — a brand-new
> store, or one every product belongs to, is invisible. "In the JPD store" and
> "in no store at all" are **indistinguishable through the API**.
>
> There is also no endpoint that can settle it: `/store/store`, `/stores/`,
> `/store/store/list` and `/store/store/{id}` all 404 (re-verified 2026-08-09),
> and `?storeId=` on `/products/` is **silently ignored** — a deliberately fake
> id returns the same 53 products, so a 200 there means nothing.
>
> **The only proof is the GHL UI**: Payments → Stores → open the store and look
> for the products. Do not treat absence from `excludedStoreIds` as evidence.

| | |
|---|---|
| **Type** | Human task — **blocking for build phase 1's exit criterion** |
| **Platform** | GoHighLevel (the existing tenant — do NOT create a new sub-account) |
| **Time** | ~10 minutes |
| **Decision it implements** | DEC-002 — same payment provider, same Stripe account, same GHL tenant, **new store** |
| **Verify with** | `/opt/jarvis/bin/jpd connectors` → `ghl_payments` reaches `live` |

---

## Why you are doing this and not me

**There is no stores API.** Verified from this host on 2026-08-07 with the live
Private Integrations key:

| Endpoint | Result |
|---|---|
| `GET /store/store` | **404** `Cannot GET /store/store` |
| `GET /stores` | **404** |
| `GET /store/store/list` | **404** |
| `GET /products/` | 200 — 53 products (so the key and transport are fine) |

Creating a store is UI-only, exactly like connecting a payment provider and
creating a funnel page. This is the same class of limit already recorded for
landing pages in CHECKPOINT §4.10.

## Why a *separate* store rather than reusing the existing one

The tenant is **co-tenanted**. Of 53 products, most belong to an unrelated
hiking/tours business. There are already two stores:

| Store ID | Products that exclude it | i.e. products that live in the *other* store |
|---|---|---|
| `XnQcO1I5FSsEJ8Q2if6A` | 43 | Pimlico's ~9 |
| `E80QUWhL04Xn8avZ3zwi` | 9 | the other business's ~43 |

Store membership **is** readable via `excludedStoreIds` on each product, so a
third store gives JPD a reliable, machine-checkable namespace. Without it,
"our products" is a guess based on names — and a wrong guess here means JPD
mutating or pricing someone else's live catalogue.

> ⚠️ **The other business's products are real and live.** Do not edit, reprice,
> or delete anything you did not create. `PUT /products/{id}` is a **REPLACE**:
> a partial payload blanks the name and store assignment.

---

## Steps

1. Log in to GoHighLevel and switch to the **Nevasca / Pimlico** sub-account
   (location id `Zf5YnZeLjmXO7HrU6OUp` — confirm this in the URL before doing
   anything else).

2. Left sidebar → **Payments** → **Stores**.

3. Click **Create Store** (or **New Store**).

4. Fill in:
   - **Store name:** `JPD` — keep it exactly this, the runbook and the checkpoint both use it
   - **Store slug / URL:** `jpd`
   - **Domain:** the same domain the existing store uses (`nevasca.pro`)
   - Leave everything else at its default.

5. **Save.**

6. Open the new store's settings and copy its **store ID** from the browser URL.
   It is a 20-character alphanumeric string, the same shape as
   `XnQcO1I5FSsEJ8Q2if6A`.

7. Confirm a payment provider is attached: **Payments → Integrations** should
   show **Stripe** connected.
   > This is already true for this tenant — a live product page renders a real
   > Stripe checkout (verified 2026-08-07). You are confirming, not connecting.
   >
   > ⚠️ Do **not** judge this from the API: `GET /payments/integrations/provider/whitelabel`
   > returns `providers: []` because it lists *whitelabel* providers only. An
   > earlier session read that as "payments are disconnected" and was wrong.

---

## Then tell JPD about it

Two values go into `/opt/jarvis/platform/docker/.env`:

```bash
JARVIS_GHL_STORE_ID=<the 20-char store id from step 6>
JARVIS_GHL_CHECKOUT_BASE=https://nevasca.pro
```

Then propagate them — **`docker service update --force` does NOT re-read
`env_file`**, so they must be added explicitly:

```bash
docker service update \
  --env-add JPD_GHL_STORE_ID=<store id> \
  --env-add JPD_GHL_CHECKOUT_BASE=https://nevasca.pro \
  jarvis_commerce
```

---

## Verification — three checks, in order

**1. The connector reaches `live`.** It cannot until the store id is set — the
contract test refuses without one, deliberately, so an offer can never be
created into a co-tenanted catalogue:

```bash
/opt/jarvis/bin/jpd connectors | grep ghl_payments
```
Expect `live`. If it says `dormant`, run the contract test and read its detail —
it names the missing thing rather than just failing.

**2. A real €1 test purchase, one per tier.** This is build phase 1's actual
exit criterion, and nothing else substitutes for it:

```bash
# creates a throwaway 3-tier ladder at €1 / €3.50 / €12.50 against the new store
/opt/jarvis/bin/jpd commerce test-ladder --base-minor 100
```
Buy each tier with a real card, then confirm:
```bash
/opt/jarvis/bin/jpd commerce orders --last 3
```
Every row must show `signature_valid=t`, `amount_matched=t`,
`status=fulfilled`, and a delivered fulfilment for **every tier at or below**
the one purchased (Instructions delivers Roadmap too).

**3. The webhook is actually reachable.** GHL must be able to call us. Today
port 8904 is deliberately **unpublished** — there is nothing to receive yet.
Going live needs an nginx vhost + certbot for a webhook hostname, the shared
secret set on both sides, and then a re-probe from a TEST-NET-3 source.

---

## What breaks if you skip this

`ghl_payments` stays `dormant`, so every commerce step returns
`skipped_dormant` — visibly, in `jpd resume`, not silently. Nothing fabricates
an offer and nothing pretends to have sold anything. The rest of phase 1 is
already proven against the stub provider; this is the step that converts
"proven" into "has actually taken money".
