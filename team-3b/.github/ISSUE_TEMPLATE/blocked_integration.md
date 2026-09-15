name: Blocked on Integration
description: Use this template when work is blocked waiting for Team A (Contracts) or Team C (Agents).
title: "[BLOCKED] "
labels: ["blocked", "integration"]
body:
  - type: markdown
    attributes:
      value: "Use this template to flag interface drift or missing deliverables from other teams."
  - type: dropdown
    id: team
    attributes:
      label: Which team is blocking this?
      options:
        - Team A (Smart Contracts)
        - Team C (Agent Logic)
        - Both
    validations:
      required: true
  - type: textarea
    id: description
    attributes:
      label: Description of Blocker
      description: What exactly are we waiting for? (e.g., specific ABI, endpoint, payload format)
    validations:
      required: true
  - type: textarea
    id: impact
    attributes:
      label: Impact
      description: Which subsystem feature or wave is halted by this?
    validations:
      required: true
