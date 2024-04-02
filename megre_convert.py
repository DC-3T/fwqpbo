import glob
import nibabel as nib
import numpy as np
from scipy.io import savemat

mag_list = glob.glob("C:\\READYCOM_PT1\\SE6_SE7_dixon\\*e?.nii")
phase_list = glob.glob("C:\\READYCOM_PT1\\SE6_SE7_dixon\\*e?_ph.nii")

mag_data = []
for ima in mag_list:
    mag_temp = nib.load(ima)
    mag_data.append(mag_temp.get_fdata())
mag_concat = np.stack(mag_data, axis=-1)

phase_data = []
for ima in phase_list:
    phase_temp = nib.load(ima)
    phase_data.append(phase_temp.get_fdata())
phase_concat = np.stack(phase_data, axis=-1)

phase_rad = (phase_concat - 2048.) / 2048. * np.pi
cplx_data = mag_concat * np.exp(phase_rad * 1j)
cplx_data = np.expand_dims(cplx_data, axis=3)

megre_dict = {}
megre_dict["imDataParams"] = {}
megre_dict["imDataParams"]["TE"] = np.array([0.00131, 0.00275, 0.00419, 0.00563, 0.00707, 0.00851])
megre_dict["imDataParams"]["images"] = cplx_data
megre_dict["imDataParams"]["FieldStrength"] = np.array(1.5, dtype='uint8')
megre_dict["imDataParams"]["PrecessionIsClockwise"] = np.array([[1]], dtype='int16')
savemat('megre_data_20240202.mat', megre_dict)
