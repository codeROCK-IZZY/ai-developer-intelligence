import { formatPrice } from "../utils/format";

export interface CartItem {
  sku: string;
  name: string;
  unitPriceCents: number;
  quantity: number;
}

/** Shopping cart kept in the browser until checkout. */
export class Cart {
  private items = new Map<string, CartItem>();

  addItem(item: CartItem): void {
    const existing = this.items.get(item.sku);
    if (existing) {
      existing.quantity = Math.min(existing.quantity + item.quantity, 20);
    } else {
      this.items.set(item.sku, { ...item });
    }
  }

  removeItem(sku: string): void {
    this.items.delete(sku);
  }

  /** Cart total in cents, before tax and discounts. */
  totalCents(): number {
    let total = 0;
    for (const item of this.items.values()) total += item.unitPriceCents * item.quantity;
    return total;
  }

  summary(): string {
    return `${this.items.size} items, ${formatPrice(this.totalCents())}`;
  }

  toOrderItems(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [sku, item] of this.items) out[sku] = item.quantity;
    return out;
  }
}
