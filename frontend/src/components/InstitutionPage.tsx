import { Link } from "react-router-dom";
import { OrgChart } from "./OrgChart";

interface Props {
  institutionId: string;
}

/**
 * Institution-level route. For now this is just a header + the org chart
 * focused on the given org id. The old detail panel depended on the retired
 * seed graph; a proper institution profile view will be a follow-up once the
 * profile endpoints exist.
 */
export function InstitutionPage({ institutionId }: Props) {
  return (
    <div className="institution-page">
      <header className="page-toolbar">
        <nav className="breadcrumb" aria-label="Breadcrumb">
          <Link to="/">Atlas</Link>
          <span className="breadcrumb__sep" aria-hidden="true">/</span>
          <span className="breadcrumb__current">Organization</span>
        </nav>
        <Link className="button-secondary" to={`/?focus=${institutionId}`}>
          Open in explorer
        </Link>
      </header>

      <div className="institution-page__canvas">
        <OrgChart focusId={institutionId} minHeight="calc(100vh - 180px)" />
      </div>
    </div>
  );
}
