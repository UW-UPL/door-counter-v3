/**
 * Netlify Function: pending-devices
 * Fetches pending BLE devices using bot credentials (works with private repos)
 */

exports.handler = async (event, context) => {
    const GITHUB_BOT_TOKEN = process.env.GITHUB_BOT_TOKEN;
    const PUBLIC_REPO_OWNER = process.env.PUBLIC_REPO_OWNER;
    const PUBLIC_REPO_NAME = process.env.PUBLIC_REPO_NAME;

    // Debug: Check if env vars are loaded
    console.log('Environment check:', {
        hasToken: !!GITHUB_BOT_TOKEN,
        tokenLength: GITHUB_BOT_TOKEN?.length || 0,
        owner: PUBLIC_REPO_OWNER || 'UNDEFINED',
        repo: PUBLIC_REPO_NAME || 'UNDEFINED'
    });

    const headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'GET, OPTIONS'
    };

    if (event.httpMethod === 'OPTIONS') {
        return {
            statusCode: 200,
            headers,
            body: ''
        };
    }
    if (event.httpMethod !== 'GET') {
        return {
            statusCode: 405,
            headers,
            body: JSON.stringify({ error: 'Method not allowed' })
        };
    }

    // Check if environment variables are configured
    if (!GITHUB_BOT_TOKEN || !PUBLIC_REPO_OWNER || !PUBLIC_REPO_NAME) {
        return {
            statusCode: 500,
            headers,
            body: JSON.stringify({
                error: 'Missing environment variables',
                details: {
                    GITHUB_BOT_TOKEN: !!GITHUB_BOT_TOKEN,
                    PUBLIC_REPO_OWNER: PUBLIC_REPO_OWNER || 'not set',
                    PUBLIC_REPO_NAME: PUBLIC_REPO_NAME || 'not set'
                }
            })
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