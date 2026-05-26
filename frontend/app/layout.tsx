import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Atlas",
  description: "The intelligence platform that sees through what your tools can't.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
