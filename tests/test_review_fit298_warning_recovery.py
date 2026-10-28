"""Reviewer test (FIT-298): the persistence warning must clear once storage
works again and when the draft is cleared, and must not show on success."""
from js_runtime import run_app_js


def test_warning_hidden_on_success_shown_on_failure_cleared_on_recovery_and_clear():
    hidden = run_app_js(
        ["saveActiveWorkoutDraft", "clearActiveWorkoutDraft", "state"],
        """
const warning = { hidden: true, textContent: '' };
sandbox.elements['active-workout-persistence-warning'] = warning;
let failWrites = false;
sandbox.localStorage.setItem = () => { if (failWrites) throw new Error('QuotaExceededError'); };
sandbox.__fitSet.currentActiveWorkoutDraftScope(() => 'user:1');
e.state.activeWorkout = { id: 'w', focus: 'F', dirty: true, exercises: [] };
const seen = [];
const snap = () => seen.push(warning.hidden);
const save = () => e.saveActiveWorkoutDraft({ syncDom: false });
save(); snap();                      // success: stays hidden
failWrites = true; save(); snap();   // failure: shown
failWrites = false; save(); snap();  // storage recovered: hidden again
failWrites = true; save(); snap();   // failing again: shown
e.clearActiveWorkoutDraft(); snap(); // draft cleared (workout finished/discarded): hidden
process.stdout.write(JSON.stringify(seen));
""",
        mocks=["currentActiveWorkoutDraftScope"],
    )

    assert hidden == [True, False, True, False, True]
