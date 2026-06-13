import type { ReactNode } from "react";

/** 2-column CSS grid for dashboard widgets. A widget spans 1 or 2 columns via
 *  the `cd-span-{n}` class on its frame (see styles.css). */
export default function WidgetGrid({ children }: { children: ReactNode }) {
  return <div className="cd-grid">{children}</div>;
}
