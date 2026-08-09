/**
 * Coauthorship ties for scatter overlay when a person is focused.
 */

import { gql } from "@apollo/client";

export const PERSON_COAUTHOR_TIES = gql`
  query PersonCoauthorTies($personId: ID!) {
    personCoauthorTies(personId: $personId) {
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
