import { Link, useLocation } from "react-router-dom";
import { EmptyState } from "../components/ui";
import { usePageTitle } from "../hooks/usePageTitle";

export function NotFound() {
  usePageTitle("Page not found");
  const { pathname } = useLocation();
  return (
    <section className="not-found">
      <header className="masthead panel">
        <div className="masthead-accent" aria-hidden="true" />
        <div><p className="eyebrow">404</p><h1>Page not found</h1></div>
      </header>
      <EmptyState title={`Nothing at ${pathname}`}
                  action={<p><Link to="/">Fleet overview</Link> · <Link to="/cases">Cases</Link>
                    {" · "}<Link to="/about">About</Link></p>}>
        The address may be mistyped, or the page may have moved.
      </EmptyState>
    </section>
  );
}
