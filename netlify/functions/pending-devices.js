/**
 * Netlify Function: pending-devices
 * Fetches pending BLE devices using bot credentials (works with private repos)
 */

exports.handler = async (event, context) => {
    // Enable CORS for cross-origin requests
    const headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'GET, OPTIONS'
    };

    // Handle preflight OPTIONS request
    if (event.httpMethod === 'OPTIONS') {
        return {
            statusCode: 200,
            headers,
            body: ''
        };
    }

    // Only accept GET requests
    if (event.httpMethod !== 'GET') {
        return {
            statusCode: 405,
            headers,
            body: JSON.stringify({ error: 'Method not allowed' })
        };
    }

    // Get environment variables
    const GITHUB_BOT_TOKEN = process.env.GITHUB_BOT_TOKEN;
    const PUBLIC_REPO_OWNER = process.env.PUBLIC_REPO_OWNER;
    const PUBLIC_REPO_NAME = process.env.PUBLIC_REPO_NAME;

    if (!GITHUB_BOT_TOKEN) {
        return {
            statusCode: 500,
            headers,
            body: JSON.stringify({ error: 'GitHub bot token not configured' })
        };
    }

    try {
        // Fetch pending.json using GitHub API with bot credentials
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

        return {
            statusCode: 200,
            headers,
            body: JSON.stringify({
                success: true,
                devices: pendingDevices
            })
        };

    } catch (error) {
        console.error('Error fetching pending devices:', error);
        return {
            statusCode: 500,
            headers,
            body: JSON.stringify({
                error: error.message,
                devices: []
            })
        };
    }
};