import { redirect } from "next/navigation";

/**
 * The former Upload & Analyze page. Creator uploads now open from Creator Analytics / Creator List (Upload creators),
 * and every upload - with live status, retry and its creators - is in History. Old bookmarks of "/" land on the dashboard.
 */
export default function RootPage() {
  redirect("/creator-analytics");
}
