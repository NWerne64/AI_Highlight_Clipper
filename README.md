# AI-driven highlights extraction from live steams
# - StreamClipper AI -

This repository contains a web app for auto highlights generation for a twitch stream live with ai!
We utilize the power of ai (PANNs and Hugging Faces) to extract features from sound and live chat to predict highlights from video game streams on twitch! <br>

To run the web app and the server follow the instructions below.

## Features
* Upload and manage your own stream videos
* Record live streams directly live from Twitch (via FFmpeg and streamlink)
* Import Twitch VODs directly from Twitch (including chat logs)
* Audio analysis using panns-inference / librosa
* Chat analysis using Huggingface sentiment models
* Generation of highligh clips based on weighted scoring


## Installation

### Prerequisites
* Python 3.8+ installed and in PATH
* pip installed
* [FFmpeg](https://ffmpeg.org/) (including ffprobe) installed and available in PATH
* *Optional: Git for cloning the repository*

### Instructions
You need to do the following to use the app:

#### Basic Setup (Clone, Install, Configure)
1. Clone the repository `git clone <repo-url>` or simply over GitHub: *codes -> local -> clone -> HTTPS -> copy url*
2. Install all necessary libraries for the application to work using the `pip install –r requirements.txt` command
3. Prepare the database using the `python manage.py makemigrations` command
4. Finish preparing the database using the `python manage.py migrate` command
#### Set up Twitch API Access
5. Set up config for the twitch integration. For this open the file `.env` and enter the following data
![](/images/twitch_config.PNG)
   1. To get the id's, visit [https://dev.twitch.tv/](https://dev.twitch.tv/) 
   2. login to twitch and click authorize *(you need to SetUp two-factor-authentication)*
   3. click "Your Console"
   4. Click "register your application"
   5. fill out the form for example like this:
   ![](/images/twitch_dev_fill_out.PNG)
   6. Click "create", and then "manage"
   7. click "New Secret"
   8. Copy "Client ID" and "Client Secret"
#### Download & Install AI Model Files (PANNs)
6. Install mandatory PANNs data: "Cnn14_mAP=0.431", through this [link](https://huggingface.co/thelou1s/panns-inference/blob/main/Cnn14_mAP%3D0.431.pth) *(warning: 327mb)*
7. Install mandatory PANNs data: "class_labels_indices.csv", through this [link](https://github.com/IBM/audioset-classification/blob/master/audioset_classify/metadata/class_labels_indices.csv)
8. Move "Cnn14_mAP=0.431.pth" and "class_labels_indices.csv" to `C:\Users\<your-username>\panns_data\` *(create yourself if necessary)*
#### Run the App
9. Now we can launch the web application. To launch the web application in the root directory of the repository, enter the command:`python manage.py runserver` 
10. Open your browser at `http://127.0.0.1:8000/` 


## App overview

When navigating to the public IP address of the web application, you will see the login/register panel, in which you can create a new user account.

When you are logged in, you can choose between 3 options:

![](/images/three_options.jpg)

* "VOD hochladen" (en="upload VOD") enables you to upload a video file from your PC directly. 
  * *Only sound will get analysed!*
* "Twitch aufnehmen" (en="record Twitch") enables you to record a real-time live twitch stream from any twitch-streamer. 
  * *Only sound will get analysed!*
* "Twitch VOD importieren" (en="import Twitch VOD") enbles you to import
  * *Sound and Chat will get analysed!*


If you upload/ download/ import/ record a stream, it will appear just below:

![](/images/existing_streams.PNG)

If you choose to actually generate highlights you have to click on "highlights & details", where you will end up in the video player site:

![](/images/video_player.PNG)

Now you can click "Highlights generieren" (en="generate highlight") and generate your first highlights after a few seconds:

![](/images/generated_highlights.PNG)

You can also read about everything important with the info page, the "?" button on the right corner of the main page!