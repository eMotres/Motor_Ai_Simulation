import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { motorLinkAvailability } from '../motorAvailability.ts';

test('missing server availability flags fail closed for configure and simulation', () => {
  assert.deepEqual(motorLinkAvailability({ die: 'D', config: 'C', duties: ['rated'] }, 'rated'), {
    configureDuty: null, pointsDuty: null, simulationDuty: null, loadDuty: null,
  });
});

test('points-only Configure opens for any stored result, never replaces the full Configure', () => {
  const motor = { die: 'D', config: 'C', available: true, can_open_points: true, results: [
    { duty: 'takeoff', has_result: false },
    { duty: 'cruise', has_result: true, can_open_configure: false },
    { duty: 'peak', has_result: true, can_open_configure: true },
  ] };
  assert.equal(motorLinkAvailability(motor, 'cruise').pointsDuty, 'cruise');
  assert.equal(motorLinkAvailability(motor, 'takeoff').pointsDuty, null);          // nothing stored for that duty
  assert.equal(motorLinkAvailability(motor, 'peak').pointsDuty, null);             // the full passport path wins
  assert.equal(motorLinkAvailability(motor, 'peak').configureDuty, 'peak');
  assert.equal(motorLinkAvailability({ ...motor, available: false }, 'cruise').pointsDuty, null);
  assert.equal(motorLinkAvailability(motor, undefined, true).pointsDuty, 'cruise');  // motor-level default: first stored point
  assert.equal(motorLinkAvailability(motor).pointsDuty, null);                       // only where the caller allows the default
  assert.equal(motorLinkAvailability({ ...motor, can_open_points: false }, undefined, true).pointsDuty, null);
});

test('an unsolved duty of an available motor can still be loaded into Simulation', () => {
  const motor = { die: 'D', config: 'C', available: true, results: [
    { duty: 'takeoff', has_result: false, can_open_simulation: false },
    { duty: 'rated', can_open_simulation: true },
  ] };
  assert.equal(motorLinkAvailability(motor, 'takeoff').loadDuty, 'takeoff');
  assert.equal(motorLinkAvailability(motor, 'rated').loadDuty, null);
  assert.equal(motorLinkAvailability(motor, 'unknown').loadDuty, null);
  assert.equal(motorLinkAvailability({ ...motor, available: false }, 'takeoff').loadDuty, null);
});

test('configure requires explicit server approval and its loadable duty', () => {
  assert.equal(motorLinkAvailability({ die: 'D', config: 'C', results: [{ duty: 'rated', can_open_configure: true }] }, 'rated').configureDuty, 'rated');
  assert.equal(motorLinkAvailability({ die: 'D', config: 'C', results: [{ duty: 'rated', can_open_configure: false }] }, 'rated').configureDuty, null);
  assert.equal(motorLinkAvailability({ die: 'D', config: 'C', can_open_configure: true }, 'rated').configureDuty, null);
  const defaultConfig = { die: 'D', config: 'C', can_open_configure: true, configure_duty: 'rated' };
  assert.equal(motorLinkAvailability(defaultConfig).configureDuty, null);
  assert.equal(motorLinkAvailability(defaultConfig, undefined, true).configureDuty, 'rated');
});

test('simulation is decided per exact duty and requires its stored-result flag', () => {
  const motor = { die: 'D', config: 'C', results: [
    { duty: 'rated', can_open_simulation: true },
    { duty: 'peak', can_open_simulation: false },
  ] };
  assert.equal(motorLinkAvailability(motor, 'rated').simulationDuty, 'rated');
  assert.equal(motorLinkAvailability(motor, 'peak').simulationDuty, null);
  assert.equal(motorLinkAvailability(motor, 'unknown').simulationDuty, null);
  const defaultSimulation = { die: 'D', config: 'C', can_open_simulation: true, simulation_duty: 'rated' };
  assert.equal(motorLinkAvailability(defaultSimulation).simulationDuty, null);
  assert.equal(motorLinkAvailability(defaultSimulation, undefined, true).simulationDuty, 'rated');
});

test('Development links load only on explicit clicks; customers are gated and history rows stay read only', () => {
  const source = readFileSync(new URL('../components/DevelopmentPage.tsx', import.meta.url), 'utf8');
  const actions = readFileSync(new URL('../MotorOpenActions.tsx', import.meta.url), 'utf8');
  const admin = readFileSync(new URL('../../../components/admin/sections/ProjectsSection.tsx', import.meta.url), 'utf8');
  assert.match(actions, /availability\.configureDuty && <Button/);
  assert.match(actions, /availability\.simulationDuty[\s\S]*\? engineer && <Button/);
  assert.match(actions, /onClick=\{\(\) => open\('configure'/);
  assert.match(actions, /onClick=\{\(\) => open\('simulation'/);
  assert.match(source, /r\.history \? null : <Stack/);
  assert.match(admin, /<MotorOpenActions motor=\{m\} allowMotorDefault engineer t=\{td\}/);
  assert.match(admin, /m\.kind !== 'history'/);
  assert.doesNotMatch(source, /handoverTip|tr\('handover'\)/);
});
