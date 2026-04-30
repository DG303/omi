import { NextRequest, NextResponse } from 'next/server';
import { verifyAdmin } from '@/lib/auth';
export const dynamic = 'force-dynamic';

const GOAFFPRO_BASE = 'https://api.goaffpro.com/v1';
const GOAFFPRO_TOKEN = process.env.GOAFFPRO_ACCESS_TOKEN || '';

async function goaffproGet(path: string) {
  const res = await fetch(`${GOAFFPRO_BASE}${path}`, {
    headers: {
      'x-goaffpro-access-token': GOAFFPRO_TOKEN,
      'Content-Type': 'application/json',
    },
  });
  if (!res.ok) {
    throw new Error(`GoAffPro error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

async function goaffproPost(path: string, body: unknown) {
  const res = await fetch(`${GOAFFPRO_BASE}${path}`, {
    method: 'POST',
    headers: {
      'x-goaffpro-access-token': GOAFFPRO_TOKEN,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`GoAffPro error ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

function parseTrafficSource(landingPage: string): { source: string; isAd: boolean } {
  if (!landingPage) return { source: 'direct', isAd: false };
  try {
    const url = new URL(landingPage);
    const params = url.searchParams;
    if (params.get('gad_source') || params.get('gclid') || params.get('gad_campaignid')) {
      return { source: 'Google Ads', isAd: true };
    }
    if (params.get('fbclid') || params.get('fb_action_ids')) {
      return { source: 'Facebook Ads', isAd: true };
    }
    if (params.get('utm_source')) {
      const medium = params.get('utm_medium') || '';
      const isAd = ['cpc', 'ppc', 'paid', 'ad', 'ads'].includes(medium.toLowerCase());
      return { source: params.get('utm_source')!, isAd };
    }
  } catch {
    // Invalid URL
  }
  return { source: 'organic', isAd: false };
}

export async function GET(request: NextRequest) {
  const authResult = await verifyAdmin(request);
  if (authResult instanceof NextResponse) return authResult;

  try {
    const { searchParams } = new URL(request.url);
    const action = searchParams.get('action') || 'pending';

    if (action === 'pending') {
      // Get pending payouts with affiliate details
      const pendingRes = await goaffproGet('/admin/payments/pending');
      const pending = pendingRes.pending || [];

      // Get order details for affiliates with pending amounts
      const enriched = await Promise.all(
        pending
          .filter((p: { pending: number }) => p.pending >= 10) // minimum payout
          .map(async (p: {
            affiliate_id: number;
            name: string;
            email: string;
            pending: number;
            total_earned: number;
            total_paid: number;
            payment_method: string;
            payment_details_data: string;
            ref_code: string;
            amounts: { sales_commission: number; mlm_reward: number };
          }) => {
            // Get orders for this affiliate to analyze traffic sources
            let orders: Array<{ conversion_details?: { landing_page?: string; referrer?: string } }> = [];
            let adOrders = 0;
            let organicOrders = 0;
            try {
              const ordersRes = await goaffproGet(
                `/admin/orders?affiliate_id=${p.affiliate_id}&status=approved&fields=id,conversion_details&limit=50`
              );
              orders = ordersRes.orders || [];
              for (const o of orders) {
                const { isAd } = parseTrafficSource(o.conversion_details?.landing_page || '');
                if (isAd) adOrders++;
                else organicOrders++;
              }
            } catch {
              // Orders fetch failed — non-fatal
            }

            // Check if affiliate has Stripe connected
            let stripeAccountId: string | null = null;
            try {
              const details = typeof p.payment_details_data === 'string'
                ? JSON.parse(p.payment_details_data)
                : p.payment_details_data || {};
              for (const val of Object.values(details)) {
                if (typeof val === 'string' && val.startsWith('acct_')) {
                  stripeAccountId = val;
                  break;
                }
              }
            } catch {
              // Parse failed
            }

            return {
              affiliate_id: p.affiliate_id,
              name: p.name,
              email: p.email,
              ref_code: p.ref_code,
              pending_amount: p.pending,
              total_earned: p.total_earned,
              total_paid: p.total_paid,
              payment_method: p.payment_method,
              stripe_account_id: stripeAccountId,
              total_orders: orders.length,
              ad_orders: adOrders,
              organic_orders: organicOrders,
              sales_commission: p.amounts?.sales_commission || 0,
            };
          })
      );

      // Sort by pending amount descending
      enriched.sort((a: { pending_amount: number }, b: { pending_amount: number }) => b.pending_amount - a.pending_amount);

      return NextResponse.json({ affiliates: enriched });
    }

    if (action === 'history') {
      const historyRes = await goaffproGet('/admin/payments?limit=50');
      return NextResponse.json(historyRes);
    }

    return NextResponse.json({ error: 'Invalid action' }, { status: 400 });
  } catch (error) {
    console.error('Affiliate payouts error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Failed to fetch data' },
      { status: 500 }
    );
  }
}

export async function POST(request: NextRequest) {
  const authResult = await verifyAdmin(request);
  if (authResult instanceof NextResponse) return authResult;

  try {
    const body = await request.json();
    const { action } = body;

    if (action === 'transfer') {
      const { affiliate_id } = body;

      if (!affiliate_id) {
        return NextResponse.json(
          { error: 'affiliate_id is required' },
          { status: 400 }
        );
      }

      // Re-fetch pending amount and Stripe account server-side (never trust client)
      const pendingRes = await goaffproGet(
        `/admin/payments/pending?affiliate_id=${affiliate_id}`
      );
      const pendingEntry = (pendingRes.pending || [])[0];
      if (!pendingEntry || pendingEntry.pending < 10) {
        return NextResponse.json(
          { error: 'No pending amount above minimum threshold' },
          { status: 400 }
        );
      }

      const amount = pendingEntry.pending;

      // Extract Stripe account ID from affiliate's payment details
      let stripeAccountId: string | null = null;
      try {
        const details = typeof pendingEntry.payment_details_data === 'string'
          ? JSON.parse(pendingEntry.payment_details_data)
          : pendingEntry.payment_details_data || {};
        for (const val of Object.values(details)) {
          if (typeof val === 'string' && (val as string).startsWith('acct_')) {
            stripeAccountId = val as string;
            break;
          }
        }
      } catch {
        // Parse failed
      }

      if (!stripeAccountId) {
        return NextResponse.json(
          { error: 'Affiliate has no Stripe account connected' },
          { status: 400 }
        );
      }

      // 1. Send Stripe Transfer
      const stripe = (await import('@/lib/stripe')).getStripe();
      const transfer = await stripe.transfers.create({
        amount: Math.round(amount * 100), // cents
        currency: 'usd',
        destination: stripeAccountId,
        metadata: { affiliate_id: String(affiliate_id) },
      });

      // 2. Mark as paid in GoAffPro
      await goaffproPost('/admin/payments', {
        payments: [
          {
            affiliate_id: String(affiliate_id),
            amount,
            payment_method: 'stripe',
            admin_note: `Stripe transfer ${transfer.id}`,
          },
        ],
      });

      return NextResponse.json({
        success: true,
        transfer_id: transfer.id,
      });
    }

    return NextResponse.json({ error: 'Invalid action' }, { status: 400 });
  } catch (error) {
    console.error('Affiliate payout transfer error:', error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : 'Transfer failed' },
      { status: 500 }
    );
  }
}
