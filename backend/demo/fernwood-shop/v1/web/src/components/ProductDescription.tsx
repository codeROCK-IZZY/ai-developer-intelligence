import type { Product } from "../api/client";

/** Product details panel. The long description comes from the CMS as HTML. */
export function ProductDescription({ product, html }: { product: Product; html: string }) {
  return (
    <section className="product">
      <h2>{product.name}</h2>
      <div className="description" dangerouslySetInnerHTML={{ __html: html }} />
    </section>
  );
}
