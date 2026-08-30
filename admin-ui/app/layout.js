import "./globals.css";

export const metadata = {
  title: "Copilot — Control Room",
  description: "Tenant access and usage control for Conversation Copilot",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        {/* Runtime-configurable API base, so a static build doesn't need
            a rebuild per deployment target — set this to your real
            server host (see infra/ Caddy snippet) before deploying. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `window.__COPILOT_ADMIN_API_BASE__ = ${JSON.stringify(
              process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000"
            )};`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
