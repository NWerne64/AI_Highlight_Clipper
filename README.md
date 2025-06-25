# AI-driven highlights extraction from live steams
# - StreamClipper AI -

This repository contains a web app for auto highlights generation for a twitch stream live with ai!
We utilize the power of ai (PANNs and Hugging Faces) to extract features from sound and live chat to predict highlights from video game streams on twitch! <br>

To run the web app and the server follow the instructions below.

## Instructions

You need to do the following to use the app:


1. Using the `pip install –r requirements.txt` command, install all the necessary libraries for the web application to work
2. Using the `python manage.py makemigrations`
3. Using the `python manage.py migrate`
4. Set up config for the twitch integration, for this open the file `.env` and enter the following data
![](/images/twitch_config.png)
5. Install mendatory PANNs data: "Cnn14_mAP=0.431", through this [link](https://huggingface.co/thelou1s/panns-inference/blob/main/Cnn14_mAP%3D0.431.pth) *(warning: 327mb)*
6. Move the "Cnn14_mAP=0.431" to `panns_data`
7. Now we can launch the web application. To launch the web application in the root directory of the repository, enter the command:`python manage.py runserver`


## App overview

When navigating to the public IP address of the web application, you will see the login/register panel, in which you can create a new user account.

When you are logged in, you can choose between 3 options:

![](/images/three_options.png)

* "VOD hochladen" (en="upload VOD") enables you to upload a video file from your PC directly. 
  * *Only sound will get analysed!*
* "Twitch aufnehmen" (en="record Twitch") enables you to record a real-time live twitch stream from any twitch-streamer. 
  * *Only sound will get analysed!*
* "Twitch VOD importieren" (en="import Twitch VOD") enbles you to import
  * *Sound and Chat will get analysed!*


If you upload/ download/ import/ record a stream, it will appear just below:

![](/images/existing_streams.png)

If you choose to actually generate highlights you have to click on "highlights & details", where you will end up in the video player site:

![](/images/video_player.png)

Now you can click "Highlights generieren" (en="generate highlight") and generate your first highlights after a few seconds:

![](/images/generated_highlights.png)

You can also read about everything important with the info page, the "?" button on the right corner of the main page!