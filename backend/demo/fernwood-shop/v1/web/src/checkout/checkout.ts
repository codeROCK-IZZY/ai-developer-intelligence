import { placeOrder, type OrderResult } from "../api/client";
import { Cart } from "../cart/cart";

export class CheckoutError extends Error {}

/** Validate the cart and submit it as an order. */
export async function submitOrder(cart: Cart, token: string, region: string, cardToken: string): Promise<OrderResult> {
  if (cart.totalCents() === 0) throw new CheckoutError("Your cart is empty");
  if (!region) throw new CheckoutError("Choose a shipping region");
  if (!cardToken) throw new CheckoutError("Payment details are missing");
  return placeOrder(token, cart.toOrderItems(), region, cardToken);
}
