import { json } from '@sveltejs/kit';
import { PUBLIC_REPO_OWNER, PUBLIC_REPO_NAME } from '$env/static/public';

/**
 * GET /api/pending-devices
 * Fetches the list of pending BLE devices from the repository
 */
export async function GET() {
    try {
        const response = await fetch(
            `https://raw.githubusercontent.com/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/refs/heads/main/data/pending.json`
        );

        if (!response.ok) {
            throw new Error(`Failed to fetch pending devices: ${response.status}`);
        }

        const pendingDevices = await response.json();

        return json({
            success: true,
            devices: pendingDevices
        }, { status: 200 });

    } catch (error) {
        console.error('Error fetching pending devices:', error);
        return json({
            error: error.message,
            devices: []
        }, { status: 500 });
    }
}