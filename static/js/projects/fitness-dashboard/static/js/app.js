/**
 * Fitness Dashboard - Exercise Swap Feature
 * Task ID: 20260329-114009-928cf7
 * 
 * Features:
 * - Preview cards with swap buttons (before starting workout)
 * - Active workout modal with swap buttons on each exercise
 * - Swap sheet modal for selecting alternatives
 * - Mid-workout exercise swapping with state preservation
 */

// ============================================================================
// Configuration & Constants
// ============================================================================

const EXERCISE_ALTERNATIVES = {
  "Bench Press": ["Incline Bench Press", "Dumbbell Press", "Push-ups", "Floor Press"],
  "Pull-ups": ["Lat Pulldown", "Chin-ups", "Inverted Rows", "Assisted Pull-ups"],
  "Overhead Press": ["Arnold Press", "DB Shoulder Press", "Landmine Press", "Push Press"],
  "Squat": ["Front Squat", "Goblet Squat", "Leg Press", "Hack Squat"],
  "Deadlift": ["Romanian Deadlift", "Trap Bar Deadlift", "Sumo Deadlift", "Rack Pulls"],
  "Barbell Row": ["Dumbbell Row", "Cable Row", "Inverted Row", "T-Bar Row"],
  "Dips": ["Push-ups", "Bench Dips", "Close-Grip Bench", "Tricep Extensions"]
};

const EXERCISE_DEFAULTS = {
  "Bench Press": { weight: 135, reps: 5 },
  "Incline Bench Press": { weight: 115, reps: 6 },
  "Dumbbell Press": { weight: 50, reps: 8 },
  "Push-ups": { weight: 0, reps: 15 },
  "Floor Press": { weight: 105, reps: 6 },
  "Pull-ups": { weight: 0, reps: 8 },
  "Lat Pulldown": { weight: 120, reps: 8 },
  "Chin-ups": { weight: 0, reps: 6 },
  "Inverted Rows": { weight: 0, reps: 12 },
  "Assisted Pull-ups": { weight: 50, reps: 8 },
  "Overhead Press": { weight: 95, reps: 6 },
  "Arnold Press": { weight: 40, reps: 8 },
  "DB Shoulder Press": { weight: 45, reps: 8 },
  "Landmine Press": { weight: 65, reps: 8 },
  "Push Press": { weight: 105, reps: 5 },
  "Squat": { weight: 185, reps: 5 },
  "Front Squat": { weight: 155, reps: 6 },
  "Goblet Squat": { weight: 50, reps: 10 },
  "Leg Press": { weight: 270, reps: 10 },
  "Hack Squat": { weight: 180, reps: 8 },
  "Deadlift": { weight: 225, reps: 5 },
  "Romanian Deadlift": { weight: 185, reps: 6 },
  "Trap Bar Deadlift": { weight: 245, reps: 5 },
  "Sumo Deadlift": { weight: 235, reps: 5 },
  "Rack Pulls": { weight: 275, reps: 5 },
  "Barbell Row": { weight: 115, reps: 6 },
  "Dumbbell Row": { weight: 50, reps: 8 },
  "Cable Row": { weight: 100, reps: 10 },
  "T-Bar Row": { weight: 90, reps: 8 },
  "Dips": { weight: 0, reps: 10 },
  "Bench Dips": { weight: 0, reps: 12 },
  "Close-Grip Bench": { weight: 95, reps: 6 },
  "Tricep Extensions": { weight: 30, reps: 10 }
};

// ============================================================================
// Global State
// ============================================================================

let currentWorkout = null;
let currentRecommendation = null;
let swapTargetIndex = null;

// Sample workout data
const sampleWorkouts = [
  {
    id: 1,
    name: "Upper Body Power",
    exercises: [
      { name: "Bench Press", target_weight: 135, target_reps: 5, sets: [] },
      { name: "Pull-ups", target_weight: 0, target_reps: 8, sets: [] },
      { name: "Overhead Press", target_weight: 95, target_reps: 6, sets: [] }
    ]
  },
  {
    id: 2,
    name: "Lower Body Power",
    exercises: [
      { name: "Squat", target_weight: 185, target_reps: 5, sets: [] },
      { name: "Deadlift", target_weight: 225, target_reps: 5, sets: [] },
      { name: "Barbell Row", target_weight: 115, target_reps: 6, sets: [] }
    ]
  }
];

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Get default weight and reps for an exercise
 * @param {string} exerciseName - Name of the exercise
 * @returns {{weight: number, reps: number}} Default values
 */
function getExerciseDefaults(exerciseName) {
  return EXERCISE_DEFAULTS[exerciseName] || { weight: 100, reps: 10 };
}

/**
 * Get alternatives for an exercise
 * @param {string} exerciseName - Name of the exercise
 * @returns {string[]} Array of alternative exercise names
 */
function getExerciseAlternatives(exerciseName) {
  return EXERCISE_ALTERNATIVES[exerciseName] || ["Alternative Exercise 1", "Alternative Exercise 2"];
}

/**
 * Render set rows for an exercise
 * @param {object} exercise - Exercise object
 * @param {number} index - Exercise index
 * @returns {string} HTML string for set rows
 */
function renderSets(exercise, index) {
  const numSets = 3;
  let html = '';
  for (let i = 0; i < numSets; i++) {
    const set = exercise.sets?.[i] || {};
    html += `
      <div class="set-row" data-set="${i}">
        <label>Set ${i + 1}</label>
        <input type="number" value="${set.weight ?? exercise.target_weight}" placeholder="lbs" data-weight>
        <input type="number" value="${set.reps ?? exercise.target_reps}" placeholder="reps" data-reps>
        <input type="checkbox" ${set.completed ? 'checked' : ''} data-complete>
      </div>
    `;
  }
  return html;
}

// ============================================================================
// Core Functions - Preview Cards (Before Starting Workout)
// ============================================================================

/**
 * Open swap sheet for preview card exercise
 * @param {number} index - Exercise index
 */
function openSwapSheet(index) {
  const exercise = sampleWorkouts[0].exercises[index];
  const alternatives = getExerciseAlternatives(exercise.name);
  
  const swapSheet = document.getElementById('swap-sheet');
  const exerciseName = document.getElementById('swap-exercise-name');
  const alternativesList = document.getElementById('alternatives-list');
  
  exerciseName.textContent = exercise.name;
  alternativesList.innerHTML = alternatives.map(alt => `
    <div class="alternative-item" onclick="performExerciseSwap('${alt}', ${index})">
      ${alt}
    </div>
  `).join('');
  
  swapSheet.style.display = 'flex';
}

/**
 * Perform exercise swap for preview card
 * @param {string} newExerciseName - New exercise name
 * @param {number} index - Exercise index
 */
function performExerciseSwap(newExerciseName, index) {
  const exercise = sampleWorkouts[0].exercises[index];
  const defaults = getExerciseDefaults(newExerciseName);
  
  exercise.name = newExerciseName;
  exercise.target_weight = defaults.weight;
  exercise.target_reps = defaults.reps;
  exercise.sets = [];
  
  // Re-render the workout card
  renderWorkoutCard();
  
  // Close swap sheet
  document.getElementById('swap-sheet').style.display = 'none';
}

/**
 * Render workout preview card
 */
function renderWorkoutCard() {
  const workout = sampleWorkouts[0];
  const container = document.querySelector('.workout-card .exercise-list');
  
  if (!container) return;
  
  container.innerHTML = workout.exercises.map((exercise, index) => `
    <div class="exercise-item">
      <span>${exercise.name} - ${exercise.target_weight} lbs x ${exercise.target_reps} reps</span>
      <button class="swap-btn" onclick="openSwapSheet(${index})" title="Swap Exercise">🔄</button>
    </div>
  `).join('');
}

// ============================================================================
// Core Functions - Active Workout Modal
// ============================================================================

/**
 * Start workout - builds active workout modal with swap buttons
 * @param {number} workoutIndex - Index of workout to start
 */
function startWorkout(workoutIndex = 0) {
  const workout = sampleWorkouts[workoutIndex];
  currentWorkout = JSON.parse(JSON.stringify(workout));
  currentRecommendation = JSON.parse(JSON.stringify(workout));
  
  const modal = document.getElementById('workout-modal');
  const exercisesContainer = document.getElementById('active-workout-exercises');
  
  // Build exercise cards with swap buttons
  exercisesContainer.innerHTML = currentWorkout.exercises.map((exercise, index) => `
    <div class="active-exercise" data-index="${index}">
      <div class="active-exercise-header">
        <h4>${exercise.name}</h4>
        <div class="exercise-target">${exercise.target_weight} lbs x ${exercise.target_reps} reps</div>
        <button class="swap-btn" onclick="openSwapModal(${index})" title="Swap Exercise">🔄</button>
      </div>
      <div class="sets-container" id="sets-${index}">
        ${renderSets(exercise, index)}
      </div>
    </div>
  `).join('');
  
  modal.style.display = 'flex';
}

/**
 * Open swap modal for active workout exercise
 * @param {number} index - Exercise index
 */
function openSwapModal(index) {
  swapTargetIndex = index;
  const exercise = currentWorkout.exercises[index];
  const alternatives = getExerciseAlternatives(exercise.name);
  
  const swapModal = document.getElementById('swap-modal');
  const exerciseName = document.getElementById('swap-exercise-name');
  const alternativesList = document.getElementById('alternatives-list');
  
  exerciseName.textContent = exercise.name;
  alternativesList.innerHTML = alternatives.map(alt => `
    <div class="alternative-item" onclick="performSwap('${alt}', ${index})">
      ${alt}
    </div>
  `).join('');
  
  swapModal.style.display = 'flex';
}

/**
 * Perform exercise swap in active workout
 * @param {string} newExerciseName - New exercise name
 * @param {number} index - Exercise index
 */
function performSwap(newExerciseName, index) {
  const exercise = currentWorkout.exercises[index];
  const defaults = getExerciseDefaults(newExerciseName);
  
  // Update exercise
  exercise.name = newExerciseName;
  exercise.target_weight = defaults.weight;
  exercise.target_reps = defaults.reps;
  exercise.sets = []; // Reset sets for this exercise
  
  // Update currentRecommendation for final submission
  currentRecommendation.exercises[index] = { ...exercise };
  
  // Update UI for this exercise only
  const exerciseEl = document.querySelector(`.active-exercise[data-index="${index}"]`);
  if (exerciseEl) {
    const header = exerciseEl.querySelector('.active-exercise-header h4');
    const target = exerciseEl.querySelector('.exercise-target');
    const setsContainer = exerciseEl.querySelector('.sets-container');
    
    header.textContent = newExerciseName;
    target.textContent = `${defaults.weight} lbs x ${defaults.reps} reps`;
    setsContainer.innerHTML = renderSets(exercise, index);
  }
  
  // Close swap modal
  document.getElementById('swap-modal').style.display = 'none';
}

/**
 * Bind swap buttons to active workout container
 * @param {string} containerSelector - CSS selector for container
 */
function bindSwapButtons(containerSelector) {
  const container = document.querySelector(containerSelector);
  if (!container) return;
  
  const swapButtons = container.querySelectorAll('.swap-btn');
  swapButtons.forEach(btn => {
    // Buttons already have onclick handlers
  });
}

// ============================================================================
// Modal Control Functions
// ============================================================================

/**
 * Close modal by ID
 * @param {string} modalId - Modal element ID
 */
function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.style.display = 'none';
  }
}

/**
 * Complete workout
 */
function completeWorkout() {
  console.log('Workout completed:', currentRecommendation);
  closeModal('workout-modal');
  // TODO: Send to API
}

// ============================================================================

// ============================================================================
// History Filter Functions
// ============================================================================
let historyData = [];
let currentMachineFilter = 'all';
let currentDateRange = 'all';

async function loadHistory() {
  try {
    const response = await fetch('/api/history-all');
    historyData = await response.json();
    renderHistoryByFilter();
  } catch (error) {
    console.error('Error loading history:', error);
  }
}

function getUniqueExercises() {
  const exercises = new Set();
  historyData.forEach(workout => {
    if (workout.exercises && Array.isArray(workout.exercises)) {
      workout.exercises.forEach(ex => {
        if (ex.machine) exercises.add(ex.machine);
      });
    }
  });
  return Array.from(exercises).sort();
}

function renderMachineFilter() {
  const container = document.getElementById('machine-filter-container');
  if (!container) return;
  
  const exercises = getUniqueExercises();
  const select = document.createElement('select');
  select.id = 'machine-filter';
  select.className = 'machine-filter-select';
  
  const allOption = document.createElement('option');
  allOption.value = 'all';
  allOption.textContent = 'All Exercises';
  select.appendChild(allOption);
  
  exercises.forEach(ex => {
    const option = document.createElement('option');
    option.value = ex;
    option.textContent = ex;
    select.appendChild(option);
  });
  
  select.value = currentMachineFilter;
  select.addEventListener('change', (e) => {
    currentMachineFilter = e.target.value;
    renderHistoryByFilter();
  });
  
  container.innerHTML = '';
  container.appendChild(select);
}

function renderHistoryByFilter() {
  const container = document.getElementById('history-results');
  if (!container) return;
  
  if (!historyData || historyData.length === 0) {
    container.innerHTML = '<p class="no-data">No workout history found.</p>';
    return;
  }
  
  let filtered = [...historyData];
  const now = new Date();
  const dateRanges = { '7d': 7, '30d': 30, '90d': 90 };
  
  if (dateRanges[currentDateRange]) {
    const cutoff = new Date(now.getTime() - dateRanges[currentDateRange] * 24 * 60 * 60 * 1000);
    filtered = filtered.filter(w => new Date(w.date) >= cutoff);
  }
  
  if (currentMachineFilter !== 'all') {
    filtered = filtered.map(workout => {
      if (!workout.exercises) return { ...workout, exercises: [] };
      const hasMachine = workout.exercises.some(ex => ex.machine === currentMachineFilter);
      if (!hasMachine) return { ...workout, exercises: [] };
      return { ...workout, exercises: workout.exercises.filter(ex => ex.machine === currentMachineFilter) };
    }).filter(w => w.exercises && w.exercises.length > 0);
  }
  
  renderWorkoutHistory(filtered);
}

function renderWorkoutHistory(workouts) {
  const container = document.getElementById('history-results');
  if (!container) return;
  
  if (!workouts || workouts.length === 0) {
    container.innerHTML = '<p class="no-data">No workouts match the current filters.</p>';
    return;
  }
  
  container.innerHTML = workouts.map(workout => `
    <div class="workout-card history-card">
      <div class="workout-header">
        <h4>${workout.name || 'Workout'}</h4>
        <span class="workout-date">${new Date(workout.date).toLocaleDateString()}</span>
      </div>
      <div class="workout-exercises">
        ${workout.exercises.map(ex => `
          <div class="exercise-row ${currentMachineFilter !== 'all' ? 'highlighted' : ''}">
            <strong>${ex.machine}</strong> - ${ex.sets || 0} sets
          </div>
        `).join('')}
      </div>
    </div>
  `).join('');
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-content').forEach(tab => {
    tab.classList.remove('active');
  });
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.classList.remove('active');
  });
  
  document.getElementById(tabName).classList.add('active');
  const btn = document.querySelector(`button[onclick="switchTab('${tabName}')"]`);
  if (btn) btn.classList.add('active');
  
  if (tabName === 'history') {
    loadHistory();
    renderMachineFilter();
  }
}

function setDateRange(range) {
  currentDateRange = range;
  document.querySelectorAll('.date-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === range);
  });
  renderHistoryByFilter();
}

// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  // Event listeners for close buttons
  document.querySelectorAll('.close-btn').forEach(btn => {
    btn.addEventListener('click', function() {
      const modal = this.closest('.modal');
      if (modal) modal.style.display = 'none';
    });
  });
  
  // Initialize preview card
  renderWorkoutCard();
});

// ============================================================================
// Global Exports (for HTML onclick handlers)
// ============================================================================

window.startWorkout = startWorkout;
window.openSwapSheet = openSwapSheet;
window.performExerciseSwap = performExerciseSwap;
window.openSwapModal = openSwapModal;
window.performSwap = performSwap;
window.closeModal = closeModal;
window.completeWorkout = completeWorkout;
window.bindSwapButtons = bindSwapButtons;
window.getExerciseDefaults = getExerciseDefaults;
window.loadHistory = loadHistory;
window.renderMachineFilter = renderMachineFilter;
window.switchTab = switchTab;
window.setDateRange = setDateRange;

// ============================================================================
// History Filter Functions - Machine/Exercise Filter
// Task: 20260329-130000-stats-filter
// ============================================================================

// Global state variables
let historyData = [];
let currentMachineFilter = 'all';
let currentDateRange = 'all';
let currentActivityType = 'workouts';

// Mock data for demonstration
const MOCK_HISTORY_DATA = [
  { id: 1, date: '2026-03-29T10:00:00Z', name: 'Upper Body', activityType: 'workouts', exercises: [{ machine: 'Bench Press', muscle_group: 'Chest', sets: 4 }, { machine: 'Pull-ups', muscle_group: 'Back', sets: 3 }] },
  { id: 2, date: '2026-03-28T14:30:00Z', name: 'Lower Body', activityType: 'workouts', exercises: [{ machine: 'Squat', muscle_group: 'Legs', sets: 5 }, { machine: 'Deadlift', muscle_group: 'Back', sets: 3 }] },
  { id: 3, date: '2026-03-27T09:00:00Z', name: 'Cardio', activityType: 'cardio', exercises: [{ machine: 'Treadmill', muscle_group: 'Cardio', sets: 1 }] }
];

async function loadHistory() {
  try {
    const response = await fetch('/api/history-all');
    historyData = response.ok ? await response.json() : MOCK_HISTORY_DATA;
  } catch (e) { historyData = MOCK_HISTORY_DATA; }
  renderHistoryByFilter();
}

function getUniqueExercises() {
  const exercises = new Set();
  historyData.filter(w => w.activityType === 'workouts').forEach(workout => {
    if (workout.exercises) workout.exercises.forEach(ex => { if (ex.machine) exercises.add(ex.machine); });
  });
  return Array.from(exercises).sort();
}

function renderMachineFilter() {
  const container = document.getElementById('machine-filter-container');
  if (!container) return;
  if (currentActivityType !== 'workouts') { container.style.display = 'none'; return; }
  container.style.display = 'block';
  const exercises = getUniqueExercises();
  container.innerHTML = '<div class="machine-filter-wrapper"><select id="machine-filter" class="machine-filter-select"><option value="all">All Exercises</option>' + exercises.map(ex => '<option value="' + ex + '">' + ex + '</option>').join('') + '</select><button id="machine-filter-clear" class="machine-filter-clear" title="Clear">×</button></div>';
  const select = container.querySelector('#machine-filter');
  select.value = currentMachineFilter;
  select.addEventListener('change', e => { currentMachineFilter = e.target.value; renderHistoryByFilter(); });
  container.querySelector('#machine-filter-clear').addEventListener('click', () => { currentMachineFilter = 'all'; select.value = 'all'; renderHistoryByFilter(); });
}

function renderHistoryByFilter() {
  const container = document.getElementById('history-results');
  if (!container) return;
  if (!historyData.length) { container.innerHTML = '<p class="no-data">No history found.</p>'; return; }
  let filtered = historyData.filter(w => w.activityType === currentActivityType || !w.activityType);
  const now = new Date(), dateRanges = { '7d': 7, '30d': 30, '90d': 90 };
  if (dateRanges[currentDateRange]) {
    const cutoff = new Date(now.getTime() - dateRanges[currentDateRange] * 86400000);
    filtered = filtered.filter(w => new Date(w.date) >= cutoff);
  }
  if (currentMachineFilter !== 'all' && currentActivityType === 'workouts') {
    filtered = filtered.map(workout => {
      if (!workout.exercises) return { ...workout, exercises: [] };
      const has = workout.exercises.some(ex => ex.machine === currentMachineFilter);
      if (!has) return { ...workout, exercises: [] };
      return { ...workout, exercises: workout.exercises.filter(ex => ex.machine === currentMachineFilter) };
    }).filter(w => w.exercises && w.exercises.length > 0);
  }
  renderWorkoutHistory(filtered);
}

function renderWorkoutHistory(workouts) {
  const container = document.getElementById('history-results');
  if (!container) return;
  if (!workouts.length) { container.innerHTML = '<p class="no-data">No matches.</p>'; return; }
  container.innerHTML = workouts.map(workout => '<div class="workout-card history-card"><div class="workout-header"><h4>' + (workout.name || 'Workout') + '</h4><span class="workout-date">' + new Date(workout.date).toLocaleDateString() + '</span></div><div class="workout-exercises">' + (workout.exercises || []).map(ex => '<div class="exercise-row' + (currentMachineFilter !== 'all' ? ' highlighted' : '') + '"><strong>' + ex.machine + '</strong> - ' + (ex.sets || 0) + ' sets</div>').join('') + '</div></div>').join('');
}

function setActivityType(type) {
  currentActivityType = type;
  currentMachineFilter = 'all';
  renderMachineFilter();
  renderHistoryByFilter();
}

function setDateRange(range) {
  currentDateRange = range;
  document.querySelectorAll('.date-filter-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.range === range);
  });
  renderHistoryByFilter();
}

// Export to window
if (typeof window !== 'undefined') {
  window.loadHistory = loadHistory;
  window.renderMachineFilter = renderMachineFilter;
  window.setActivityType = setActivityType;
  window.setDateRange = setDateRange;
}
