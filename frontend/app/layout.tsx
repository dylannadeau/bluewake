import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "bluewake — vessel sanctions screening",
  description:
    "IMO-keyed vessel risk lookup with audit-ready screening reports. Decision-support only.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <span className="brand">bluewake</span>
          <span className="tagline">maritime sanctions screening</span>
        </header>
        <main>{children}</main>
        <footer className="disclaimer">
          Outputs are decision-support and audit-trail records generated from
          third-party data, not legal determinations. Findings require review
          by a qualified compliance officer; absence of findings is not
          clearance.
        </footer>
      </body>
    </html>
  );
}
