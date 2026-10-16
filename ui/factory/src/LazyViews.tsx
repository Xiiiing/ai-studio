import { lazy, Suspense } from "react";
import { Loading } from "./ui";
import type { RecordData } from "./api";

const ModelView = lazy(() =>
  import("./Models").then((module) => ({ default: module.Models })),
);
const MaterialView = lazy(() =>
  import("./MaterialLibrary").then((module) => ({
    default: module.MaterialLibrary,
  })),
);

export function Models(props: { notify: (message: string) => void }) {
  return (
    <Suspense fallback={<Loading />}>
      <ModelView {...props} />
    </Suspense>
  );
}
export function MaterialLibrary(props: {
  project?: RecordData;
  notify: (message: string) => void;
}) {
  return (
    <Suspense fallback={<Loading />}>
      <MaterialView {...props} />
    </Suspense>
  );
}
