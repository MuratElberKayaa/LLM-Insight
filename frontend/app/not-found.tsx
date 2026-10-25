import Link from "next/link";

export default function NotFound() {
  return (
    <div className="rounded-lg border border-line bg-surface px-6 py-10 text-center">
      <p className="text-base font-semibold text-ink">Page not found</p>
      <p className="mt-2 text-sm text-ink-2">Built pages: Overview and Traces.</p>
      <Link href="/" className="mt-4 inline-block rounded-md border border-line px-3 py-1.5 text-sm text-ink-2 hover:bg-raised hover:text-ink">
        Go to Overview
      </Link>
    </div>
  );
}
