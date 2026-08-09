import { Navigate, useParams } from "react-router-dom";

export function ExploreGraphPage() {
  const { nodeId } = useParams<{ nodeId: string }>();
  const focus = nodeId ?? "person-alice";
  return <Navigate to={`/?focus=${encodeURIComponent(focus)}`} replace />;
}
