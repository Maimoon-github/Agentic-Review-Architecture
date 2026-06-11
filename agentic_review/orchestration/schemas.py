from typing import List, Optional
from pydantic import BaseModel, Field

class PlanOutput(BaseModel):
    """Schema for the Strategic Planner agent."""
    plan: str = Field(description="The high-level strategy and breakdown of sub-tasks.")
    success_criteria: List[str] = Field(description="List of benchmarks for success.")
    structural_critique: str = Field(description="Initial structural expectations for the Writer.")

class ReasonerOutput(BaseModel):
    """Schema for the Analytical Reasoner agent."""
    analysis: str = Field(description="Detailed logical analysis of the proposed plan.")
    gaps: List[str] = Field(description="Identified flaws, contradictions, or missing details.")
    reasoning_critique: str = Field(description="Logical standards the output must adhere to.")
    sound: bool = Field(description="Whether the plan is logically sound and complete.")

class FinalCritiqueOutput(BaseModel):
    """Schema for the Synthesis node."""
    merged_critique: str = Field(description="Consolidated quality standard.")
    checklist: List[str] = Field(description="A binary checklist for the Reviewer to follow.")

class WriterOutput(BaseModel):
    """Schema for the Content Writer agent."""
    draft: str = Field(description="The complete, polished content draft.")
    notes_for_editor: str = Field(description="Specific instructions for the Editor sub-agent.")
    ready_for_review: bool = Field(description="Whether the writer is satisfied with the draft.")

class EditorOutput(BaseModel):
    """Schema for the Content Editor agent."""
    refined_text: str = Field(description="The modified, added, or deleted content pass results.")
    editorial_notes: str = Field(description="Explanation of the changes made.")

class ReviewerOutput(BaseModel):
    """Schema for the Quality Reviewer agent."""
    decision: str = Field(description="Binary decision: MATCH or MISMATCH.")
    structural_issues: bool = Field(description="Flag for structural/scope failures.")
    logical_issues: bool = Field(description="Flag for logical consistency failures.")
    annotations: str = Field(description="Detailed feedback for revision cycles.")
