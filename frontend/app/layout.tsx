import type { Metadata } from "next";
import { Fira_Code, Fira_Sans } from "next/font/google";
import "./globals.css";
import { HealthStrip } from "@/components/HealthStrip";
import { NavLinks } from "@/components/NavLinks";
import { ThemeToggle } from "@/components/ThemeToggle";

const sans = Fira_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-fira-sans",
  display: "swap",
});
const mono = Fira_Code({
  subsets: ["latin"],
  weight: ["400", "600"],
  variable: "--font-fira-code",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "RecoveryOS", template: "%s | RecoveryOS" },
  description:
    "A release-preparation agent that resumes after crashes without repeating a side effect or skipping a human approval.",
};

// Sets the theme before first paint so there is no flash. Storage may be blocked; fall back to system.
const themeScript = `try{var t=localStorage.getItem("recoveryos-theme");if(t==="light"||t==="dark"){document.documentElement.dataset.theme=t}}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        <header className="site-header">
          <div className="shell">
            <a className="wordmark" href="/">
              Recovery<span>OS</span>
            </a>
            <NavLinks />
            <ThemeToggle />
          </div>
        </header>
        <HealthStrip />
        <main id="main" tabIndex={-1}>
          <div className="shell">{children}</div>
        </main>
      </body>
    </html>
  );
}
