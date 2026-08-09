import { gql } from "@apollo/client";

export const PERSPECTIVE = gql`
  query Perspective($personId: ID!) {
    perspective(personId: $personId) {
      focusId
      alterCount
      maxPaperCount
      alters {
        personId
        label
        institution
        rank
        importance
        group
        hop
        paperCount
        relation
      }
    }
  }
`;

export interface PerspectiveAlter {
  personId: string;
  label: string;
  institution: string | null;
  rank: string | null;
  importance: number;
  group: number;
  hop: number;
  paperCount: number | null;
  relation: string | null;
}

export interface PerspectiveData {
  perspective: {
    focusId: string;
    alterCount: number;
    maxPaperCount: number;
    alters: PerspectiveAlter[];
  };
}
