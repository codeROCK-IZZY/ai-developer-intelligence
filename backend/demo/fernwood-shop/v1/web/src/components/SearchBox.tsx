import { useState } from "react";
import { searchProducts, type Product } from "../api/client";

/** Search form; reports matching products to the parent. */
export function SearchBox({ onResults }: { onResults: (products: Product[]) => void }) {
  const [query, setQuery] = useState("");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (query.trim().length < 2) return;
    onResults(await searchProducts(query.trim()));
  }

  return (
    <form onSubmit={submit}>
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search gear" />
      <button type="submit">Search</button>
    </form>
  );
}
