export const ONBOARDING_STORAGE_KEY = "elmonte-onboarding-complete";

export type TourPlacement = "top" | "bottom" | "left" | "right" | "center";

export type TourStep = {
  id: string;
  target?: string;
  title: string;
  body: string;
  placement?: TourPlacement;
};

export const TOUR_STEPS: TourStep[] = [
  {
    id: "welcome",
    target: "brand",
    title: "Welcome to El Monte",
    body: "This is a research relationship atlas. Explore how people, labs, and institutions connect — every relationship is evidence-backed.",
    placement: "bottom",
  },
  {
    id: "search",
    target: "search",
    title: "Start with search",
    body: "Look up a researcher, university, department, or lab. Pick a result to set your focus and begin an investigation.",
    placement: "bottom",
  },
  {
    id: "scatter",
    target: "scatter",
    title: "People map",
    body: "Each point is a researcher. Closer points are more structurally related; color can show similarity groups or institution; larger points have more impact.",
    placement: "left",
  },
  {
    id: "org-chart",
    target: "org-chart",
    title: "Investigation trace",
    body: "Start at a university (Stanford, Berkeley, …), then pick a school or department — Economics, Business, MCB. Keep drilling down to people and their advisees.",
    placement: "right",
  },
  {
    id: "profile",
    title: "Person profile",
    body: "When you focus on a person, a profile drawer opens with their timeline, papers, and connections. Click related people to keep exploring.",
    placement: "center",
  },
  {
    id: "evidence",
    target: "evidence",
    title: "Evidence-aware",
    body: "Relationships come from sourced records, not guesses. You're all set — reopen this guide anytime from the header.",
    placement: "bottom",
  },
];

export function hasCompletedOnboarding(): boolean {
  try {
    return localStorage.getItem(ONBOARDING_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function markOnboardingComplete(): void {
  try {
    localStorage.setItem(ONBOARDING_STORAGE_KEY, "1");
  } catch {
    // Ignore storage failures (private browsing, etc.)
  }
}

export function clearOnboardingComplete(): void {
  try {
    localStorage.removeItem(ONBOARDING_STORAGE_KEY);
  } catch {
    // Ignore storage failures
  }
}
