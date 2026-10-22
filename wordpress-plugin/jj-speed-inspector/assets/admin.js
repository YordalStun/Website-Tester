/* Speed Inspector: runs the test one small step at a time and shows progress. */
(function () {
	'use strict';

	var runButton = document.getElementById('jjsi-run');
	if (!runButton) {
		return;
	}
	var progress = document.getElementById('jjsi-progress');
	var fill = document.getElementById('jjsi-progress-fill');
	var status = document.getElementById('jjsi-status');
	var frame = document.getElementById('jjsi-frame');

	function call(data) {
		var body = new FormData();
		body.append('action', 'jjsi_step');
		body.append('nonce', JJSI.nonce);
		Object.keys(data).forEach(function (key) {
			body.append(key, data[key]);
		});
		return fetch(JJSI.ajax, { method: 'POST', body: body, credentials: 'same-origin' })
			.then(function (response) {
				return response.json().catch(function () {
					throw new Error('The server returned an unexpected answer (HTTP ' + response.status + ')');
				});
			})
			.then(function (json) {
				if (!json || !json.success) {
					throw new Error(json && json.data ? json.data : 'Unknown error');
				}
				return json.data;
			});
	}

	function wait(ms) {
		return new Promise(function (resolve) { setTimeout(resolve, ms); });
	}

	// If the server can't visit its own pages (some hosts block that), the browser visits them instead.
	function visitInFrame(url) {
		return new Promise(function (resolve) {
			var done = false;
			var finish = function () {
				if (!done) {
					done = true;
					resolve();
				}
			};
			frame.onload = finish;
			frame.src = url;
			setTimeout(finish, 60000);
		});
	}

	function profileWithFallback(step) {
		return call({ 'do': 'profile', url: step.url }).then(function (result) {
			if (result.ok) {
				return result;
			}
			return visitInFrame(result.frame_url).then(function () {
				var attempts = 0;
				var tryCollect = function () {
					attempts++;
					return call({ 'do': 'collect', url: step.url, run: result.run }).then(function (collected) {
						if (collected.ok || attempts >= 4) {
							if (!collected.ok) {
								return call({ 'do': 'note', text: 'Test visit to ' + step.url + ' failed (' + result.error + ')' });
							}
							return collected;
						}
						return wait(1000).then(tryCollect);
					});
				};
				return tryCollect();
			});
		});
	}

	// A normal visit timed by the browser as a logged-out visitor (no cookies), if the server couldn't do it.
	function visitWithFallback(step) {
		return call({ 'do': 'visit', url: step.url }).then(function (result) {
			if (result.ok) {
				return result;
			}
			var start = performance.now();
			return fetch(step.url, { credentials: 'omit', cache: 'no-store' })
				.then(function (response) {
					return response.text().then(function () {
						return call({ 'do': 'record_visit', url: step.url, ms: performance.now() - start, status: response.status });
					});
				})
				.catch(function () {
					return null;
				});
		});
	}

	function label(step) {
		if (step['do'] === 'visit') {
			return 'Normal visit to ' + step.url;
		}
		if (step['do'] === 'profile') {
			return 'Measuring everything WordPress does on ' + step.url;
		}
		if (step['do'] === 'baseline') {
			return 'Timing how fast the server reaches its own website';
		}
		if (step['do'] === 'checks') {
			return 'Checking the server, database, caching and scheduled tasks';
		}
		return 'Writing the report';
	}

	runButton.addEventListener('click', function () {
		runButton.disabled = true;
		progress.hidden = false;
		fill.style.width = '2%';
		status.textContent = 'Starting...';
		var extra = document.getElementById('jjsi-extra').value || '';

		call({ 'do': 'begin', extra: extra })
			.then(function (plan) {
				var steps = plan.steps;
				var chain = Promise.resolve();
				steps.forEach(function (step, i) {
					chain = chain.then(function () {
						status.textContent = label(step) + ' (' + (i + 1) + ' of ' + steps.length + ')';
						var work;
						if (step['do'] === 'profile') {
							work = profileWithFallback(step);
						} else if (step['do'] === 'visit') {
							work = visitWithFallback(step);
						} else {
							work = call({ 'do': step['do'], url: step.url || '' });
						}
						return work.then(function () {
							fill.style.width = Math.round(((i + 1) / steps.length) * 100) + '%';
						});
					});
				});
				return chain;
			})
			.then(function () {
				status.textContent = 'Done - loading the report...';
				window.location.reload();
			})
			.catch(function (error) {
				status.textContent = 'The test stopped: ' + error.message;
				runButton.disabled = false;
			});
	});
})();
