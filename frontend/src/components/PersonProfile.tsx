import { Navigate } from "react-router-dom";

interface Props {
  personId: string;
}

/**
 * Route-level person view. The full profile (career timeline, publications,
 * personal network) renders in the side panel of the home graph, which opens
 * automatically for `?focus=p:…` — so this route is a redirect rather than a
 * second, drifting implementation of the same UI.
 */
export function PersonProfile({ personId }: Props) {
  if (!personId.startsWith("p:")) {
    return <Navigate to="/" replace />;
  }
  return <Navigate to={`/?focus=${personId}`} replace />;
}
