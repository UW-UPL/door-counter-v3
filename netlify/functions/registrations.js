/**
 * Creates device registration pull requests using GitHub bot account
 */

exports.handler = async (event, context) => {
    const GITHUB_BOT_TOKEN = process.env.GITHUB_BOT_TOKEN;
    const PUBLIC_REPO_OWNER = process.env.PUBLIC_REPO_OWNER;
    const PUBLIC_REPO_NAME = process.env.PUBLIC_REPO_NAME;

    const headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'POST, OPTIONS'
    };

    if (event.httpMethod === 'OPTIONS') {
        return { statusCode: 200, headers, body: "" };
    }
    if (event.httpMethod !== 'POST') {
        return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' })};
    }

    // Helper function for GitHub API calls
    const githubAPI = async (path, options = {}) => {
        const response = await fetch(`https://api.github.com${path}`, {
            ...options,
            headers: {
                'Authorization': `Bearer ${GITHUB_BOT_TOKEN}`,
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'Content-Type': 'application/json',
                ...options.headers
            },
            body: options.body ? JSON.stringify(options.body) : undefined
        });

        if (!response.ok) {
            const error = await response.text();
            throw new Error(`GitHub API error: ${response.status} - ${error}`);
        }

        return response.json();
    };

    try {
        const { name, soundFile, passkey, pairedAt, sharePresence } = JSON.parse(event.body);

        if (!name || !passkey || !pairedAt) {
            return {
                statusCode: 400,
                headers,
                body: JSON.stringify({ error: 'Missing required fields' })
            };
        }

        // Get the latest commit SHA from main branch
        const ref = await githubAPI(`/repos/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/git/ref/heads/main`);
        const baseSha = ref.object.sha;

        // Create a new branch
        const branchName = `registration-${Date.now()}`;
        await githubAPI(`/repos/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/git/refs`, {
            method: 'POST',
            body: {
                ref: `refs/heads/${branchName}`,
                sha: baseSha
            }
        });

        // Get the current registrations.json file
        let existingContent = { registrations: [] };
        let existingSha = null;

        try {
            const fileData = await githubAPI(`/repos/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/contents/data/registrations.json?ref=${branchName}`);

            existingSha = fileData.sha;
            existingContent = JSON.parse(Buffer.from(fileData.content, 'base64').toString());

        } catch (error) {
           return {
                statusCode: 404,
                headers,
                body: JSON.stringify({
                    success: false,
                    error: "Registration file not found"
                })
           };
        }

        // Add new registration
        existingContent.registrations.push({
            passkey: passkey,
            paired_at: pairedAt,
            name: name,
            sound_file: soundFile || '',
            share_presence: sharePresence || false
        });

        // Update registrations.json
        const updatedContent = Buffer.from(JSON.stringify(existingContent, null, 2)).toString('base64');

        const registrationBody = {
            message: `Registration for ${name}`,
            content: updatedContent,
            branch: branchName,
        };

        if (existingSha != null) {
            registrationBody.sha = existingSha;
        }

        await githubAPI(`/repos/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/contents/data/registrations.json`, {
            method: 'PUT',
            body: registrationBody
        });

        // Create pull request
        const pr = await githubAPI(`/repos/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/pulls`, {
            method: 'POST',
            body: {
                title: `Registration for ${name}`,
                head: branchName,
                base: 'main',
                body: `Adding device registration:\n- Passkey: ${passkey}\n- Name: ${name}\n- Sound: ${soundFile || 'None'}\n- Share Presence: ${sharePresence ? 'Yes' : 'No'}\n- Paired at: ${pairedAt}\n\nCreated via BLE registration bot.`,
                maintainer_can_modify: true
            }
        });

        return {
            statusCode: 200,
            headers,
            body: JSON.stringify({
                success: true,
                prUrl: pr.html_url,
                prNumber: pr.number
            })
        };

    } catch (error) {
        console.error('Registration error:', error);
        return {
            statusCode: 500,
            headers,
            body: JSON.stringify({
                success: false,
                error: error.message
            })
        };
    }

    
};