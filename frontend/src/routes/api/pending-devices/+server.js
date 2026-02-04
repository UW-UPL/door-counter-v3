import { json } from '@sveltejs/kit';
import { GITHUB_BOT_TOKEN } from '$env/static/private';
import { PUBLIC_REPO_OWNER, PUBLIC_REPO_NAME } from '$env/static/public';

/**
 * GET /api/pending-devices
 * Fetches the list of pending BLE devices from the repository using bot credentials
 */
export async function GET() {
    try {
        // Fetch pending.json
        const response = await fetch(
            `https://api.github.com/repos/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/contents/data/pending.json`,
            {
                headers: {
                    'Authorization': `Bearer ${GITHUB_BOT_TOKEN}`,
                    'Accept': 'application/vnd.github+json',
                    'X-GitHub-Api-Version': '2022-11-28'
                }
            }
        );

        if (!response.ok) {
            throw new Error(`Failed to fetch pending devices: ${response.status}`);
        }

        const fileData = await response.json();

        // Decode base64 content
        const pendingDevices = JSON.parse(Buffer.from(fileData.content, 'base64').toString());

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