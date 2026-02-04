<script>
	import { onMount } from 'svelte';

	// State variables
	let pendingDevices = [];
	let status = '';
	let loading = false;

	// Form fields
	let selectedPasskey = '';
	let selectedPairedAt = '';
	let name = '';
	let soundFile = '';
	let sharePresence = false;

	// Poll for new devices every 10 seconds
	onMount(() => {
		fetchPendingDevices();
		
		const interval = setInterval(fetchPendingDevices, 10000);

		return () => clearInterval(interval);
	});

	// Fetch pending devices from backend API
	async function fetchPendingDevices() {
		try {
			const response = await fetch('/api/pending-devices');
			const data = await response.json();

			if (data.success) {
				pendingDevices = data.devices || [];
			} else {
				console.error('Failed to fetch pending devices:', data.error);
				pendingDevices = [];
			}
		} catch (error) {
			console.error('Error fetching pending devices:', error);
			pendingDevices = [];
		}
	}

	// Select a device from the pending list
	function selectDevice(device) {
		selectedPasskey = device.passkey;
		selectedPairedAt = device.paired_at;
	}

	// Create a registration pull request
	async function createPR() {
		if (!selectedPasskey || !selectedPairedAt) {
			status = 'Please select a device first';
			return;
		}

		if (!name.trim()) {
			status = 'Please enter your name';
			return;
		}

		loading = true;
		status = 'Creating registration PR...';

		try {
			const response = await fetch('/api/registrations', {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					name: name.trim(),
					soundFile: soundFile || '',
					passkey: selectedPasskey,
					pairedAt: selectedPairedAt,
					sharePresence: sharePresence
				})
			});

			const data = await response.json();

			if (data.success) {
				status = `Success! Pull request created: ${data.prUrl}`;
				
				selectedPasskey = '';
				selectedPairedAt = '';
				name = '';
				soundFile = '';
				sharePresence = false;

				await fetchPendingDevices();
			} else {
				status = `Error: ${data.error}`;
			}
		} catch (error) {
			status = `Failed to create PR: ${error.message}`;
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>BLE Device Registration</title>
</svelte:head>

<main>
	<h1>UPL Door Counter V3</h1>

	<div>
		<h2>List of Pending Devices</h2>

		{#if pendingDevices.length > 0}
			{#each pendingDevices as device}
				<div class:selected={selectedPasskey === device.passkey}>
					<strong>Passkey:</strong> {device.passkey}<br>
					<strong>Paired at:</strong> {new Date(device.paired_at).toLocaleString()}<br>
					<button
						on:click={() => selectDevice(device)}
						disabled={loading || selectedPasskey === device.passkey}
					>
						{selectedPasskey === device.passkey ? 'Selected' : 'Select This Device'}
					</button>
				</div>
			{/each}
		{:else}
			<p>No pending devices found. Devices will appear here after Bluetooth pairing.</p>
		{/if}
	</div>

	<div>
		<h2>Register Device</h2>

		{#if selectedPasskey}
			<p><strong>Selected:</strong> {selectedPasskey} (paired at {new Date(selectedPairedAt).toLocaleString()})</p>
		{:else}
			<p>Please select a device from the list above.</p>
		{/if}

		<form on:submit|preventDefault={createPR}>
			<div>
				<label for="name">Your Name:</label>
				<input
					id="name"
					type="text"
					bind:value={name}
					placeholder="Enter your name"
					disabled={!selectedPasskey || loading}
					required
				/>
			</div>

			<div>
				<label for="soundFile">Notification Sound (Optional):</label>
				<select
					id="soundFile"
					bind:value={soundFile}
					disabled={!selectedPasskey || loading}
				>
					<option value="">None</option>
					<option value="bell.mp3">Bell</option>
					<option value="chime.mp3">Chime</option>
					<option value="ding.mp3">Ding</option>
					<option value="notification.mp3">Notification</option>
				</select>
			</div>

			<div>
				<label for="sharePresence">
					<input
						id="sharePresence"
						type="checkbox"
						bind:checked={sharePresence}
						disabled={!selectedPasskey || loading}
					/>
					Share Presence
				</label>
			</div>

			<button
				type="submit"
				disabled={!selectedPasskey || !name.trim() || loading}
			>
				{loading ? 'Creating PR...' : 'Create Registration PR'}
			</button>
		</form>
	</div>

	{#if status}
		<div>
			{status}
			{#if status.includes('https://')}
				<br>
				<a href={status.split(' ').find(word => word.startsWith('https://'))} target="_blank" rel="noopener">
					Open Pull Request
				</a>
			{/if}
		</div>
	{/if}
</main>