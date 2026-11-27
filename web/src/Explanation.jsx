import { useMemo } from "react";
import PromptCopy from "./PromptCopy.jsx";
import { buildExplanationPrompt } from "./prompt.js";

export default function Explanation({ problem, result }) {
  const prompt = useMemo(() => buildExplanationPrompt(problem, result), [problem, result]);
  return <PromptCopy prompt={prompt} />;
}
