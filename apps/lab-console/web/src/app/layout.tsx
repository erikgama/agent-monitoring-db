import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  title: "Agent Monitoring DB — Mission Control",
  description: "Canvas operacional do laboratório multiagente MySQL HeatWave",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
