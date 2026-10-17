import { lazy, Suspense } from "react";
import { Heading, Loading } from "./ui";
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
    <Suspense
      fallback={
        <>
          <Heading eyebrow="" title="模型与连接" />
          <Loading />
        </>
      }
    >
      <ModelView {...props} />
    </Suspense>
  );
}
export function MaterialLibrary(props: {
  project?: RecordData;
  notify: (message: string) => void;
}) {
  return (
    <Suspense
      fallback={
        props.project ? (
          <Loading />
        ) : (
          <>
            <Heading eyebrow="" title="素材库" />
            <Loading />
          </>
        )
      }
    >
      <MaterialView {...props} />
    </Suspense>
  );
}
