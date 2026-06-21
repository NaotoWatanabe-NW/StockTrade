import type { Metadata } from "next";
import "./globals.css";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "StockTrade 取引記録",
  description: "保有銘柄の管理と約定履歴・損益の記録",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>
        <Nav />
        <main className="container">{children}</main>
      </body>
    </html>
  );
}
