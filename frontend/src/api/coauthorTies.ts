/**
 * Coauthorship ties for scatter overlay when a person is focused.
 */

import { gql } from "@apollo/client";

export const PERSON_COAUTHOR_TIES = gql`
  query PersonCoauthorTies($personId: ID!, $view: String!) {
    personCoauthorTies(personId: $personId, view: $view) {
      personId
      paperCount
    }
  }
`;

export interface CoauthorTie {
  personId: string;
  paperCount: number;
}

export interface PersonCoauthorTiesData {
  personCoauthorTies: CoauthorTie[];
}
