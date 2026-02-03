import { json } from '@sveltejs/kit';
import { GITHUB_BOT_TOKEN } from '$env/static/private';
import { PUBLIC_REPO_OWNER, PUBLIC_REPO_NAME } from '$env/static/public';

/**
 * Helper function to make GitHub API calls
 */
async function githubAPI(path, options = {}) {
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
}

/**
 * POST /api/registrations
 * Creates a new device registration PR
 */
export async function POST({ request }) {
    try {
        const {
            name,
            soundFile,
            passkey,
            pairedAt,
            sharePresence
        } = await request.json();

        if (!name || !passkey || !pairedAt) {
            return json({error: 'Missing required fields'}, { status: 400 });
        }

        // Get the latest commit SHA from the default branch
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

        // Step 4: Get the current registrations.json file
        let existingContent = { registrations: [] };
        let existingSha = null;

        try {
            const fileData = await githubAPI(`/repos/${PUBLIC_REPO_OWNER}/${PUBLIC_REPO_NAME}/contents/data/registrations.json?ref=${branchName}`);
            
            existingSha = fileData.sha;
            existingContent = JSON.parse(Buffer.from(fileData.content, 'base64').toString());
        
        } catch (error) {
            console.log(error);
            // Probably because the registration file doesn't exist, just append to an empty list
        }

        // Add new registration
        existingContent.registrations.push({
            passkey: passkey,
            paired_at: pairedAt,
            name: name,
            sound_file: soundFile || '',
            share_presence: sharePresence || false
        });

        // Update/create registrations.json
        const updatedContent = Buffer.from(JSON.stringify(existingContent, null, 2)).toString('base64');
        
        const registrationBody = {
            message: `Registration for ${name}`,
            content: updatedContent,
            branch: branchName,
        }

        if (existingSha != null) {
            registrationBody.sha = existingSha
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
                body: `Adding device registration:\n- Passkey: ${passkey}\n- Name: ${name}\n- Sound: ${soundFile || 'None'}\n- Share Presence: ${sharePresence ? 'Yes' : 'No'}\n- Paired at: ${pairedAt}\n\nCreated with 🧀 via UPL Door Counter Bot.`,
                maintainer_can_modify: true
            }
        });

        return json({
            success: true,
            prUrl: pr.html_url
        }, { status: 200 });

    } catch (error) {
        console.error('Registration error:', error);
        return json({
            error: error.message
        }, { status: 500 });
    }
}