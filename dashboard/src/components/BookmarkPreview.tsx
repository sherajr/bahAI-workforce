import { useState } from "react";
import { imageUrl } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "./ui";

function Panel({ label, src }: { label: string; src: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="flex flex-col items-center gap-2">
      <span className="text-xs uppercase tracking-widest text-slate-500">{label}</span>
      {src && !failed ? (
        <img
          src={src}
          alt={`Bookmark ${label}`}
          onError={() => setFailed(true)}
          className="max-h-96 rounded-lg border border-slate-800 object-contain shadow-lg"
        />
      ) : (
        <div className="flex h-96 w-32 items-center justify-center rounded-lg border border-dashed border-slate-700 text-center text-xs text-slate-600">
          Not rendered
        </div>
      )}
    </div>
  );
}

// Front + back halves side by side. Paths may be web paths (/outputs/..) or local paths.
export function BookmarkPreview({
  frontPath,
  backPath,
  originalPath,
}: {
  frontPath?: string | null;
  backPath?: string | null;
  originalPath?: string | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Bookmark preview — 2″ × 6″</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap items-start justify-center gap-8">
          <Panel label="Front (quote)" src={imageUrl(frontPath)} />
          <Panel label="Back (art)" src={imageUrl(backPath)} />
          {originalPath && <Panel label="Full artwork" src={imageUrl(originalPath)} />}
        </div>
      </CardContent>
    </Card>
  );
}
