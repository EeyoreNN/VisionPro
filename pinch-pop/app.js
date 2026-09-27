// Page wiring for Pinch Pop: buttons, the in-page HUD, and entering WebXR.

import { PinchPop } from './game.js';

const $ = (selector) => document.querySelector(selector);
const game = new PinchPop($('#game'));
window.pinchPop = game;   // handy from the Web Inspector
const statusLine = $('#status');
const overlay = $('#overlay');
const hud = $('#hud');

let xrMode = null;

game.addEventListener('update', ({ detail }) => {
  $('#hud-score').textContent = detail.score;
  $('#hud-time').textContent = Math.max(0, detail.timeLeft);
  $('#hud-combo').textContent = `×${detail.combo}`;
  hud.hidden = detail.state !== 'playing';
});

game.addEventListener('over', ({ detail }) => {
  $('#overlay-title').textContent = detail.isBest ? `New best: ${detail.score}!` : `${detail.score} points`;
  $('#overlay-text').textContent = detail.isBest ? 'That’s your best round yet.' : `Your best is ${detail.best}. One more?`;
  $('#overlay-play').textContent = 'Play again';
  overlay.hidden = false;
});

game.addEventListener('pop', ({ detail }) => {
  const float = $('#float-score');
  float.textContent = detail.points > 0 ? `+${detail.points}` : `${detail.points}`;
  float.dataset.type = detail.type;
  float.classList.remove('show');
  void float.offsetWidth;          // restart the animation
  float.classList.add('show');
});

game.addEventListener('xrstart', () => {
  statusLine.textContent = 'Pinch anywhere to start a round. Press the Digital Crown to come back.';
  overlay.hidden = true;
});
game.addEventListener('xrend', () => {
  statusLine.textContent = `Welcome back. Best score: ${game.best}.`;
  overlay.hidden = false;
});

function startHere() {
  overlay.hidden = true;
  game.start();
  $('#game').scrollIntoView({ behavior: 'smooth', block: 'center' });
}

$('#overlay-play').addEventListener('click', startHere);
$('#play-here').addEventListener('click', startHere);
$('#play-xr').addEventListener('click', async () => {
  try {
    await game.enterXR(xrMode);
  } catch (error) {
    console.warn(error);
    statusLine.textContent = 'Couldn’t start the immersive session. Check that Safari is allowed to use WebXR, then try again.';
  }
});

PinchPop.xrMode().then((mode) => {
  xrMode = mode;
  if (mode) {
    $('#play-xr').hidden = false;
    $('#play-xr span').textContent = mode === 'immersive-ar' ? 'Play in your room' : 'Play in Vision Pro';
    statusLine.textContent = mode === 'immersive-ar'
      ? 'Bubbles will appear in your real room.'
      : 'Opens a dreamy dusk world around you. Your hands do the popping.';
  } else {
    statusLine.textContent = 'On Apple Vision Pro, open this page in Safari to play with your hands. Here, click the bubbles.';
  }
});
